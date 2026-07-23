import { AgentError, describeError, isAbortError, redactAgentError } from "../core/errors.ts";
import type { AgentMessage, Usage } from "../core/messages.ts";
import type {
  ModelEvent,
  ModelFinishReason,
  ModelProvider,
  ModelRequest,
  ProviderContext,
} from "../core/provider.ts";
import { parseSseStream } from "./sse.ts";

/**
 * OpenAI-compatible Chat Completions 流式 Provider。
 *
 * POST {baseUrl}/chat/completions，stream=true；
 * 把厂商 SSE 事件转换为内部 ModelEvent。
 * API Key 只出现在请求头中，不写入事件、日志或错误文本。
 */
export class OpenAICompatibleProvider implements ModelProvider {
  readonly id = "openai-compatible";

  /**
   * 对外接口：委托给内部实现，并对任何抛出的 AgentError 统一脱敏，
   * 确保 context.apiKey 无论来自 HTTP error body、协议 payload 还是
   * 底层 Error.message，都不会出现在 AgentError.message 中。
   */
  async *stream(request: ModelRequest, context: ProviderContext): AsyncGenerator<ModelEvent> {
    try {
      yield* this.streamInternal(request, context);
    } catch (error) {
      if (error instanceof AgentError) {
        throw redactAgentError(error, context.apiKey);
      }
      throw error;
    }
  }

  private async *streamInternal(
    request: ModelRequest,
    context: ProviderContext,
  ): AsyncGenerator<ModelEvent> {
    const url = `${context.baseUrl.replace(/\/$/, "")}/chat/completions`;
    let response: Response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${context.apiKey}`,
        },
        body: JSON.stringify(buildRequestBody(request)),
        // 禁止自动跟随 30x：即便初始 baseUrl 命中 Gateway allowlist，
        // 允许端点仍可能重定向到内网地址，绕过 URL 策略造成 SSRF。
        // redirect:"error" 让 fetch 在收到重定向时直接抛错，由下方 catch
        // 归一为 provider_http_error 并在 stream() 出口统一脱敏。
        redirect: "error",
        ...(context.signal ? { signal: context.signal } : {}),
      });
    } catch (error) {
      if (isAbortError(error)) {
        throw new AgentError("aborted", "模型请求已被取消", { cause: error });
      }
      throw new AgentError("provider_http_error", `无法连接模型服务：${describeError(error)}`, {
        cause: error,
      });
    }

    if (!response.ok) {
      const detail = await safeReadBody(response);
      throw new AgentError(
        "provider_http_error",
        `模型服务返回 HTTP ${response.status}${detail ? `：${detail}` : ""}`,
        { status: response.status },
      );
    }
    if (!response.body) {
      throw new AgentError("provider_protocol_error", "模型服务没有返回流式响应体");
    }

    const toolCalls = new Map<number, { id: string; name: string; arguments: string }>();
    let finishReason: ModelFinishReason | undefined;
    let usage: Usage | undefined;

    try {
      for await (const payload of parseSseStream(response.body)) {
        if (payload.trim() === "[DONE]") {
          break;
        }

        let chunk: CompletionChunk;
        try {
          chunk = JSON.parse(payload) as CompletionChunk;
        } catch (error) {
          throw new AgentError(
            "provider_protocol_error",
            `无法解析模型流式响应片段：${truncate(payload, 200)}`,
            { cause: error },
          );
        }

        const choice = chunk.choices?.[0];
        const delta = choice?.delta;

        if (typeof delta?.content === "string" && delta.content.length > 0) {
          yield { type: "text_delta", delta: delta.content };
        }

        if (Array.isArray(delta?.tool_calls)) {
          for (const fragment of delta.tool_calls) {
            const index = typeof fragment.index === "number" ? fragment.index : 0;
            let entry = toolCalls.get(index);
            if (!entry) {
              entry = { id: "", name: "", arguments: "" };
              toolCalls.set(index, entry);
              yield {
                type: "tool_call_start",
                index,
                id: typeof fragment.id === "string" ? fragment.id : "",
                name: typeof fragment.function?.name === "string" ? fragment.function.name : "",
              };
            }
            if (typeof fragment.id === "string" && fragment.id && !entry.id) {
              entry.id = fragment.id;
            }
            if (typeof fragment.function?.name === "string") {
              entry.name += fragment.function.name;
            }
            if (
              typeof fragment.function?.arguments === "string" &&
              fragment.function.arguments.length > 0
            ) {
              entry.arguments += fragment.function.arguments;
              yield {
                type: "tool_call_arguments_delta",
                index,
                delta: fragment.function.arguments,
              };
            }
          }
        }

        if (typeof choice?.finish_reason === "string" && choice.finish_reason) {
          finishReason = mapFinishReason(choice.finish_reason);
        }
        if (chunk.usage) {
          usage = mapUsage(chunk.usage);
        }
      }
    } catch (error) {
      if (isAbortError(error)) {
        throw new AgentError("aborted", "模型请求已被取消", { cause: error });
      }
      if (error instanceof AgentError) {
        throw error;
      }
      throw new AgentError(
        "provider_protocol_error",
        `读取模型流式响应失败：${describeError(error)}`,
        { cause: error },
      );
    }

    // 按 index 数值升序发出 tool_call_end，即便分片乱序到达
    // （例如 index=1 先于 index=0），也保证 Agent 顺序执行的确定性。
    const orderedIndices = [...toolCalls.keys()].sort((a, b) => a - b);
    for (const index of orderedIndices) {
      const entry = toolCalls.get(index);
      if (!entry) {
        continue;
      }
      yield {
        type: "tool_call_end",
        index,
        id: entry.id || `tool_call_${index + 1}`,
        name: entry.name,
        arguments: entry.arguments,
      };
    }
    if (usage) {
      yield { type: "usage", usage };
    }
    yield {
      type: "finish",
      reason: finishReason ?? (toolCalls.size > 0 ? "tool_calls" : "other"),
    };
  }
}

interface CompletionChunk {
  choices?: Array<{
    delta?: {
      content?: string | null;
      tool_calls?: Array<{
        index?: number;
        id?: string;
        function?: { name?: string; arguments?: string };
      }>;
    };
    finish_reason?: string | null;
  }>;
  usage?: WireUsage | null;
}

interface WireUsage {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

function buildRequestBody(request: ModelRequest): Record<string, unknown> {
  const body: Record<string, unknown> = {
    model: request.model.id,
    messages: request.messages.map(toWireMessage),
    stream: true,
  };
  if (request.model.maxTokens !== undefined) {
    body.max_tokens = request.model.maxTokens;
  }
  if (request.tools.length > 0) {
    body.tools = request.tools.map((tool) => ({
      type: "function",
      function: {
        name: tool.name,
        description: tool.description,
        parameters: tool.parameters,
      },
    }));
  }
  return body;
}

function toWireMessage(message: AgentMessage): Record<string, unknown> {
  switch (message.role) {
    case "system":
      return { role: "system", content: message.content };
    case "user":
      return { role: "user", content: message.content };
    case "assistant": {
      const wire: Record<string, unknown> = {
        role: "assistant",
        content: message.content,
      };
      if (message.toolCalls.length > 0) {
        wire.tool_calls = message.toolCalls.map((call) => ({
          id: call.id,
          type: "function",
          function: { name: call.name, arguments: call.arguments },
        }));
      }
      return wire;
    }
    case "tool":
      return {
        role: "tool",
        tool_call_id: message.toolCallId,
        content: message.content,
      };
  }
}

function mapFinishReason(reason: string): ModelFinishReason {
  switch (reason) {
    case "stop":
      return "stop";
    case "tool_calls":
      return "tool_calls";
    case "length":
      return "length";
    default:
      return "other";
  }
}

function mapUsage(wire: WireUsage): Usage {
  const usage: Usage = {};
  if (typeof wire.prompt_tokens === "number") {
    usage.inputTokens = wire.prompt_tokens;
  }
  if (typeof wire.completion_tokens === "number") {
    usage.outputTokens = wire.completion_tokens;
  }
  if (typeof wire.total_tokens === "number") {
    usage.totalTokens = wire.total_tokens;
  }
  return usage;
}

async function safeReadBody(response: Response): Promise<string> {
  try {
    const text = await response.text();
    return truncate(text.trim(), 300);
  } catch {
    return "";
  }
}

function truncate(text: string, maxLength: number): string {
  return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text;
}
