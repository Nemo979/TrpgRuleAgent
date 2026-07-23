import { describe, expect, it, vi } from "vitest";
import {
  AgentError,
  AgentRuntime,
  ToolRegistry,
  type AgentEvent,
  type ModelEvent,
  type ModelProvider,
  type ModelRequest,
  type ProviderContext,
  type ToolDefinition,
} from "../src/core/index.ts";

interface RecordedCall {
  request: ModelRequest;
  context: ProviderContext;
}

/** 按调用次序返回脚本化事件的 Provider。 */
function scriptedProvider(scripts: ModelEvent[][]): ModelProvider & { calls: RecordedCall[] } {
  const calls: RecordedCall[] = [];
  return {
    id: "scripted",
    calls,
    async *stream(request, context) {
      calls.push({ request, context });
      const script = scripts[Math.min(calls.length - 1, scripts.length - 1)] ?? [];
      for (const event of script) {
        yield event;
      }
    },
  };
}

function textScript(text: string): ModelEvent[] {
  return [
    { type: "text_delta", delta: text },
    { type: "finish", reason: "stop" },
  ];
}

function toolScript(id: string, name: string, args: string): ModelEvent[] {
  return [
    { type: "tool_call_start", index: 0, id, name },
    { type: "tool_call_arguments_delta", index: 0, delta: args },
    { type: "tool_call_end", index: 0, id, name, arguments: args },
    { type: "finish", reason: "tool_calls" },
  ];
}

function echoTool(executeMock?: ReturnType<typeof vi.fn>): {
  tool: ToolDefinition<{ value: string }>;
  execute: ReturnType<typeof vi.fn>;
} {
  const execute = executeMock ?? vi.fn().mockImplementation(async (params: { value: string }) => ({
    content: [{ type: "text", text: `echo:${params.value}` }],
  }));
  return {
    tool: {
      name: "echo",
      description: "回显输入",
      parameters: {
        type: "object",
        properties: { value: { type: "string" } },
        required: ["value"],
        additionalProperties: false,
      },
      execute: execute as ToolDefinition<{ value: string }>["execute"],
    },
    execute,
  };
}

function createRuntime(
  provider: ModelProvider,
  tools: Array<ToolDefinition<never>>,
  maxModelTurns?: number,
): AgentRuntime {
  const registry = new ToolRegistry();
  for (const tool of tools) {
    registry.register(tool);
  }
  return new AgentRuntime({
    provider,
    model: { id: "test-model" },
    baseUrl: "https://llm.example/v1",
    getApiKey: () => "test-key",
    systemPrompt: "系统提示",
    tools: registry,
    ...(maxModelTurns !== undefined ? { maxModelTurns } : {}),
  });
}

async function collect(events: AsyncIterable<AgentEvent>): Promise<AgentEvent[]> {
  const all: AgentEvent[] = [];
  for await (const event of events) {
    all.push(event);
  }
  return all;
}

function types(events: AgentEvent[]): string[] {
  return events.map((event) => event.type);
}

describe("AgentRuntime", () => {
  it("纯文本回答：turn_start -> text_delta -> turn_end", async () => {
    const provider = scriptedProvider([textScript("最终答案")]);
    const runtime = createRuntime(provider, []);

    const events = await collect(runtime.run("问题"));

    expect(types(events)).toEqual(["turn_start", "text_delta", "turn_end"]);
    expect(runtime.messages.at(-1)).toMatchObject({ role: "assistant", content: "最终答案" });
    expect(provider.calls).toHaveLength(1);
    expect(provider.calls[0]?.context.apiKey).toBe("test-key");
    expect(provider.calls[0]?.request.messages[0]).toEqual({ role: "system", content: "系统提示" });
  });

  it("单工具：执行工具、写入 tool 消息后再次调用模型", async () => {
    const provider = scriptedProvider([
      toolScript("call_1", "echo", '{"value":"hi"}'),
      textScript("完成"),
    ]);
    const { tool, execute } = echoTool();
    const runtime = createRuntime(provider, [tool as ToolDefinition<never>]);

    const events = await collect(runtime.run("问题"));

    expect(types(events)).toEqual(["turn_start", "tool_start", "tool_end", "text_delta", "turn_end"]);
    expect(execute).toHaveBeenCalledWith(
      { value: "hi" },
      expect.objectContaining({ toolCallId: "call_1" }),
    );
    expect(provider.calls).toHaveLength(2);
    const toolMessage = runtime.messages.find((message) => message.role === "tool");
    expect(toolMessage).toMatchObject({
      toolCallId: "call_1",
      toolName: "echo",
      content: "echo:hi",
      isError: false,
    });
    // 第二次模型调用能看到 assistant 工具调用与 tool 回执。
    const secondMessages = provider.calls[1]?.request.messages ?? [];
    expect(secondMessages.some((m) => m.role === "assistant")).toBe(true);
    expect(secondMessages.some((m) => m.role === "tool")).toBe(true);
  });

  it("多轮连续工具调用直到模型给出纯文本", async () => {
    const provider = scriptedProvider([
      toolScript("call_1", "echo", '{"value":"a"}'),
      toolScript("call_2", "echo", '{"value":"b"}'),
      textScript("最终"),
    ]);
    const { tool, execute } = echoTool();
    const runtime = createRuntime(provider, [tool as ToolDefinition<never>]);

    const events = await collect(runtime.run("问题"));

    expect(types(events)).toEqual([
      "turn_start",
      "tool_start", "tool_end",
      "tool_start", "tool_end",
      "text_delta", "turn_end",
    ]);
    expect(execute).toHaveBeenCalledTimes(2);
    expect(provider.calls).toHaveLength(3);
  });

  it("同一轮多个工具调用按顺序执行", async () => {
    const provider = scriptedProvider([
      [
        { type: "tool_call_end", index: 0, id: "call_1", name: "echo", arguments: '{"value":"1"}' },
        { type: "tool_call_end", index: 1, id: "call_2", name: "echo", arguments: '{"value":"2"}' },
        { type: "finish", reason: "tool_calls" },
      ],
      textScript("done"),
    ]);
    const order: string[] = [];
    const execute = vi.fn().mockImplementation(async (params: { value: string }) => {
      order.push(params.value);
      return { content: [{ type: "text", text: params.value }] };
    });
    const { tool } = echoTool(execute);
    const runtime = createRuntime(provider, [tool as ToolDefinition<never>]);

    await collect(runtime.run("问题"));
    expect(order).toEqual(["1", "2"]);
  });

  it("未知工具：产生 tool_error(unknown_tool) 并把错误回执给模型", async () => {
    const provider = scriptedProvider([
      toolScript("call_1", "nope", "{}"),
      textScript("修正后的回答"),
    ]);
    const runtime = createRuntime(provider, []);

    const events = await collect(runtime.run("问题"));

    const toolError = events.find((event) => event.type === "tool_error");
    expect(toolError).toBeDefined();
    expect(toolError?.type === "tool_error" && toolError.error.category).toBe("unknown_tool");
    expect(types(events)).toEqual(["turn_start", "tool_error", "text_delta", "turn_end"]);
    const toolMessage = runtime.messages.find((message) => message.role === "tool");
    expect(toolMessage).toMatchObject({ isError: true, toolCallId: "call_1" });
  });

  it("无效参数：不执行工具并产生 invalid_tool_call", async () => {
    const provider = scriptedProvider([
      toolScript("call_1", "echo", '{"wrong":true}'),
      textScript("ok"),
    ]);
    const { tool, execute } = echoTool();
    const runtime = createRuntime(provider, [tool as ToolDefinition<never>]);

    const events = await collect(runtime.run("问题"));

    const toolError = events.find((event) => event.type === "tool_error");
    expect(toolError?.type === "tool_error" && toolError.error.category).toBe("invalid_tool_call");
    expect(execute).not.toHaveBeenCalled();
  });

  it("参数不是合法 JSON：不执行工具并产生 invalid_tool_call", async () => {
    const provider = scriptedProvider([
      toolScript("call_1", "echo", "{broken"),
      textScript("ok"),
    ]);
    const { tool, execute } = echoTool();
    const runtime = createRuntime(provider, [tool as ToolDefinition<never>]);

    const events = await collect(runtime.run("问题"));

    const toolError = events.find((event) => event.type === "tool_error");
    expect(toolError?.type === "tool_error" && toolError.error.category).toBe("invalid_tool_call");
    expect(execute).not.toHaveBeenCalled();
  });

  it("工具抛出普通异常：tool_execution_error 且保留 cause", async () => {
    const boom = new Error("boom");
    const execute = vi.fn().mockRejectedValue(boom);
    const { tool } = echoTool(execute);
    const provider = scriptedProvider([
      toolScript("call_1", "echo", '{"value":"x"}'),
      textScript("recovered"),
    ]);
    const runtime = createRuntime(provider, [tool as ToolDefinition<never>]);

    const events = await collect(runtime.run("问题"));

    const toolError = events.find((event) => event.type === "tool_error");
    expect(toolError?.type === "tool_error" && toolError.error.category).toBe("tool_execution_error");
    expect(toolError?.type === "tool_error" && toolError.error.cause).toBe(boom);
  });

  it("工具抛出 limit_exceeded（预算超限）时保留类别", async () => {
    const execute = vi.fn().mockRejectedValue(new AgentError("limit_exceeded", "本轮最多允许搜索 3 次"));
    const { tool } = echoTool(execute);
    const provider = scriptedProvider([
      toolScript("call_1", "echo", '{"value":"x"}'),
      textScript("预算不足，直接回答"),
    ]);
    const runtime = createRuntime(provider, [tool as ToolDefinition<never>]);

    const events = await collect(runtime.run("问题"));

    const toolError = events.find((event) => event.type === "tool_error");
    expect(toolError?.type === "tool_error" && toolError.error.category).toBe("limit_exceeded");
    expect(toolError?.type === "tool_error" && toolError.error.message).toContain("最多允许搜索");
  });

  it("达到最大模型轮次时以 limit_exceeded 错误结束", async () => {
    const { tool } = echoTool();
    const provider = scriptedProvider([toolScript("call_x", "echo", '{"value":"loop"}')]);
    const runtime = createRuntime(provider, [tool as ToolDefinition<never>], 2);

    const events = await collect(runtime.run("问题"));

    const last = events.at(-1);
    expect(last?.type).toBe("error");
    expect(last?.type === "error" && last.error.category).toBe("limit_exceeded");
    expect(provider.calls).toHaveLength(2);
  });

  it("已取消的 AbortSignal：立即以 aborted 错误结束且不调用模型", async () => {
    const provider = scriptedProvider([textScript("不应出现")]);
    const runtime = createRuntime(provider, []);
    const controller = new AbortController();
    controller.abort();

    const events = await collect(runtime.run("问题", controller.signal));

    expect(types(events)).toEqual(["turn_start", "error"]);
    const last = events.at(-1);
    expect(last?.type === "error" && last.error.category).toBe("aborted");
    expect(provider.calls).toHaveLength(0);
  });

  it("AbortSignal 传递给模型请求上下文与工具执行上下文", async () => {
    const provider = scriptedProvider([
      toolScript("call_1", "echo", '{"value":"x"}'),
      textScript("done"),
    ]);
    const contexts: Array<{ signal?: AbortSignal }> = [];
    const execute = vi.fn().mockImplementation(async (_params: unknown, context: { signal?: AbortSignal }) => {
      contexts.push(context);
      return { content: [{ type: "text", text: "ok" }] };
    });
    const { tool } = echoTool(execute);
    const runtime = createRuntime(provider, [tool as ToolDefinition<never>]);
    const controller = new AbortController();

    await collect(runtime.run("问题", controller.signal));

    expect(provider.calls[0]?.context.signal).toBe(controller.signal);
    expect(contexts[0]?.signal).toBe(controller.signal);
  });

  it("工具执行期间触发取消：以 aborted 错误结束且不再调用模型", async () => {
    const controller = new AbortController();
    const execute = vi.fn().mockImplementation(async () => {
      controller.abort();
      throw new DOMException("The operation was aborted.", "AbortError");
    });
    const { tool } = echoTool(execute);
    const provider = scriptedProvider([toolScript("call_1", "echo", '{"value":"x"}')]);
    const runtime = createRuntime(provider, [tool as ToolDefinition<never>]);

    const events = await collect(runtime.run("问题", controller.signal));

    const last = events.at(-1);
    expect(last?.type === "error" && last.error.category).toBe("aborted");
    expect(provider.calls).toHaveLength(1);
  });

  it("Provider 抛出 AgentError 时透传类别", async () => {
    const provider: ModelProvider = {
      id: "failing",
      // eslint-disable-next-line require-yield
      async *stream() {
        throw new AgentError("provider_http_error", "模型服务返回 HTTP 500", { status: 500 });
      },
    };
    const runtime = createRuntime(provider, []);

    const events = await collect(runtime.run("问题"));

    const last = events.at(-1);
    expect(last?.type === "error" && last.error.category).toBe("provider_http_error");
  });

  it("usage 写入 assistant 消息", async () => {
    const provider = scriptedProvider([[
      { type: "text_delta", delta: "答" },
      { type: "usage", usage: { inputTokens: 3, outputTokens: 1 } },
      { type: "finish", reason: "stop" },
    ]]);
    const runtime = createRuntime(provider, []);

    await collect(runtime.run("问题"));

    expect(runtime.messages.at(-1)).toMatchObject({
      role: "assistant",
      usage: { inputTokens: 3, outputTokens: 1 },
    });
  });
});
