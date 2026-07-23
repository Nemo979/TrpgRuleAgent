import { AgentError, describeError, isAbortError, throwIfAborted, toAgentError } from "./errors.ts";
import type { AgentEvent } from "./events.ts";
import type { AgentMessage, AssistantMessage, ToolCall, Usage } from "./messages.ts";
import type { ModelConfig, ModelProvider } from "./provider.ts";
import { validateSchema } from "./schema.ts";
import { ToolRegistry, toolResultText, type ToolResult } from "./tools.ts";

export interface AgentRuntimeOptions {
  provider: ModelProvider;
  model: ModelConfig;
  baseUrl: string;
  /** API Key 通过执行上下文注入，不进入消息与事件。 */
  getApiKey: () => string;
  systemPrompt: string;
  tools: ToolRegistry;
  /** 单个用户问题允许的最大模型调用次数，超出即 limit_exceeded。 */
  maxModelTurns?: number;
}

const DEFAULT_MAX_MODEL_TURNS = 10;

/**
 * 业务无关的 Agent Loop：
 * 加入用户消息 -> 调用 Provider -> 流式文本/聚合工具调用 ->
 * 校验参数 -> 顺序执行工具 -> 写入 tool 消息 -> 再调模型，
 * 直到没有工具调用或达到限制。
 *
 * 所有异常都会规整为 AgentError 并以 { type: "error" } 事件结束流；
 * 首版不自动重试模型请求，避免工具重复执行。
 */
export class AgentRuntime {
  readonly messages: AgentMessage[] = [];
  private readonly options: AgentRuntimeOptions;
  private readonly maxModelTurns: number;

  constructor(options: AgentRuntimeOptions) {
    this.options = options;
    this.maxModelTurns = options.maxModelTurns ?? DEFAULT_MAX_MODEL_TURNS;
    this.messages.push({ role: "system", content: options.systemPrompt });
  }

  async *run(userInput: string, signal?: AbortSignal): AsyncGenerator<AgentEvent> {
    yield { type: "turn_start" };
    try {
      this.messages.push({ role: "user", content: userInput });

      for (let modelTurn = 0; ; modelTurn += 1) {
        if (modelTurn >= this.maxModelTurns) {
          throw new AgentError(
            "limit_exceeded",
            `单个问题最多允许 ${this.maxModelTurns} 次模型调用`,
          );
        }
        throwIfAborted(signal);

        const toolCalls: ToolCall[] = [];
        let text = "";
        let usage: Usage | undefined;

        const stream = this.options.provider.stream(
          {
            model: this.options.model,
            messages: [...this.messages],
            tools: this.options.tools.toolSpecs(),
          },
          {
            baseUrl: this.options.baseUrl,
            apiKey: this.options.getApiKey(),
            ...(signal ? { signal } : {}),
          },
        );

        for await (const event of stream) {
          if (event.type === "text_delta") {
            text += event.delta;
            yield { type: "text_delta", delta: event.delta };
          } else if (event.type === "tool_call_end") {
            toolCalls.push({ id: event.id, name: event.name, arguments: event.arguments });
          } else if (event.type === "usage") {
            usage = event.usage;
          }
        }

        const assistant: AssistantMessage = {
          role: "assistant",
          content: text,
          toolCalls,
          ...(usage ? { usage } : {}),
        };
        this.messages.push(assistant);

        if (toolCalls.length === 0) {
          yield { type: "turn_end" };
          return;
        }

        // 工具顺序执行，保持引用编号与预算的确定性。
        for (const call of toolCalls) {
          throwIfAborted(signal);
          yield* this.executeToolCall(call, signal);
        }
      }
    } catch (error) {
      yield { type: "error", error: toAgentError(error) };
    }
  }

  private async *executeToolCall(
    call: ToolCall,
    signal: AbortSignal | undefined,
  ): AsyncGenerator<AgentEvent> {
    const tool = this.options.tools.get(call.name);
    if (!tool) {
      yield this.failToolCall(call, new AgentError("unknown_tool", `未知工具：${call.name}`));
      return;
    }

    let args: unknown;
    try {
      args = call.arguments.trim() === "" ? {} : JSON.parse(call.arguments);
    } catch (error) {
      yield this.failToolCall(
        call,
        new AgentError("invalid_tool_call", `工具 ${call.name} 的参数不是合法 JSON`, {
          cause: error,
        }),
      );
      return;
    }

    const schemaErrors = validateSchema(tool.parameters, args);
    if (schemaErrors.length > 0) {
      yield this.failToolCall(
        call,
        new AgentError(
          "invalid_tool_call",
          `工具 ${call.name} 参数校验失败：${schemaErrors.join("；")}`,
        ),
      );
      return;
    }

    yield { type: "tool_start", toolCallId: call.id, toolName: call.name, arguments: args };

    let result: ToolResult;
    try {
      result = await tool.execute(args as never, {
        toolCallId: call.id,
        ...(signal ? { signal } : {}),
      });
    } catch (error) {
      if (isAbortError(error)) {
        throw new AgentError("aborted", "工具执行已被取消", { cause: error });
      }
      const agentError =
        error instanceof AgentError
          ? error
          : new AgentError("tool_execution_error", `工具 ${call.name} 执行失败：${describeError(error)}`, {
              cause: error,
            });
      if (agentError.category === "aborted") {
        throw agentError;
      }
      yield this.failToolCall(call, agentError);
      return;
    }

    this.messages.push({
      role: "tool",
      toolCallId: call.id,
      toolName: call.name,
      content: toolResultText(result),
      isError: false,
    });
    yield { type: "tool_end", toolCallId: call.id, toolName: call.name, result };
  }

  /** 写入错误 tool 消息（保证每个 tool_call 都有回执），并返回 tool_error 事件。 */
  private failToolCall(call: ToolCall, error: AgentError): AgentEvent {
    this.messages.push({
      role: "tool",
      toolCallId: call.id,
      toolName: call.name,
      content: `工具调用失败：${error.message}`,
      isError: true,
    });
    return { type: "tool_error", toolCallId: call.id, toolName: call.name, error };
  }
}
