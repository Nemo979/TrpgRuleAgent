import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AgentError,
  REDACTED,
  type ModelEvent,
  type ModelRequest,
  type ProviderContext,
} from "../src/core/index.ts";
import { OpenAICompatibleProvider } from "../src/providers/openai-compatible.ts";

const request: ModelRequest = {
  model: { id: "test-model", maxTokens: 512 },
  messages: [
    { role: "system", content: "system prompt" },
    { role: "user", content: "问题" },
  ],
  tools: [
    {
      name: "search_rules",
      description: "搜索规则",
      parameters: { type: "object", properties: { query: { type: "string" } }, required: ["query"] },
    },
  ],
};

const context: ProviderContext = {
  baseUrl: "https://llm.example/v1",
  apiKey: "secret-test-key",
};

function sseResponse(...chunks: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
  return new Response(stream, { status: 200 });
}

function data(payload: unknown): string {
  return `data: ${JSON.stringify(payload)}\n\n`;
}

async function collect(events: AsyncIterable<ModelEvent>): Promise<ModelEvent[]> {
  const all: ModelEvent[] = [];
  for await (const event of events) {
    all.push(event);
  }
  return all;
}

describe("OpenAICompatibleProvider", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("发送正确的请求并流式产出文本增量", async () => {
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(
      data({ choices: [{ delta: { content: "借机" } }] }),
      data({ choices: [{ delta: { content: "攻击" }, finish_reason: null }] }),
      data({ choices: [{ delta: {}, finish_reason: "stop" }] }),
      "data: [DONE]\n\n",
    ));
    vi.stubGlobal("fetch", fetchMock);

    const events = await collect(new OpenAICompatibleProvider().stream(request, context));

    expect(events).toEqual([
      { type: "text_delta", delta: "借机" },
      { type: "text_delta", delta: "攻击" },
      { type: "finish", reason: "stop" },
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://llm.example/v1/chat/completions");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>).authorization).toBe("Bearer secret-test-key");
    const body = JSON.parse(init.body as string);
    expect(body).toMatchObject({
      model: "test-model",
      stream: true,
      max_tokens: 512,
      tools: [{ type: "function", function: { name: "search_rules" } }],
    });
    expect(body.messages).toEqual([
      { role: "system", content: "system prompt" },
      { role: "user", content: "问题" },
    ]);
  });

  it("聚合按 index 分片到达的工具调用（含 name 分片）", async () => {
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(
      data({ choices: [{ delta: { tool_calls: [
        { index: 0, id: "call_1", function: { name: "search_", arguments: "" } },
      ] } }] }),
      data({ choices: [{ delta: { tool_calls: [
        { index: 0, function: { name: "rules", arguments: '{"qu' } },
        { index: 1, id: "call_2", function: { name: "read_rules", arguments: '{"ids":' } },
      ] } }] }),
      data({ choices: [{ delta: { tool_calls: [
        { index: 0, function: { arguments: 'ery":"x"}' } },
        { index: 1, function: { arguments: '["a"]}' } },
      ] } }] }),
      data({ choices: [{ delta: {}, finish_reason: "tool_calls" }] }),
      "data: [DONE]\n\n",
    ));
    vi.stubGlobal("fetch", fetchMock);

    const events = await collect(new OpenAICompatibleProvider().stream(request, context));

    expect(events).toContainEqual({ type: "tool_call_start", index: 0, id: "call_1", name: "search_" });
    expect(events).toContainEqual({ type: "tool_call_arguments_delta", index: 0, delta: '{"qu' });
    expect(events).toContainEqual({
      type: "tool_call_end",
      index: 0,
      id: "call_1",
      name: "search_rules",
      arguments: '{"query":"x"}',
    });
    expect(events).toContainEqual({
      type: "tool_call_end",
      index: 1,
      id: "call_2",
      name: "read_rules",
      arguments: '{"ids":["a"]}',
    });
    expect(events.at(-1)).toEqual({ type: "finish", reason: "tool_calls" });
    const endEvents = events.filter((event) => event.type === "tool_call_end");
    expect(endEvents.map((event) => event.index)).toEqual([0, 1]);
  });

  it("SSE 事件可以在任意 chunk 边界断开", async () => {
    const full = data({ choices: [{ delta: { content: "完整文本" }, finish_reason: null }] })
      + data({ choices: [{ delta: {}, finish_reason: "stop" }] })
      + "data: [DONE]\n\n";
    const pieces = [full.slice(0, 17), full.slice(17, 43), full.slice(43)];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(...pieces)));

    const events = await collect(new OpenAICompatibleProvider().stream(request, context));
    expect(events).toEqual([
      { type: "text_delta", delta: "完整文本" },
      { type: "finish", reason: "stop" },
    ]);
  });

  it("转发 usage 统计", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(
      data({ choices: [{ delta: { content: "ok" }, finish_reason: "stop" }] }),
      data({ choices: [], usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 } }),
      "data: [DONE]\n\n",
    )));

    const events = await collect(new OpenAICompatibleProvider().stream(request, context));
    expect(events).toContainEqual({
      type: "usage",
      usage: { inputTokens: 10, outputTokens: 5, totalTokens: 15 },
    });
  });

  it("非 2xx 抛出 provider_http_error 且不泄露 API Key", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response('{"error":"invalid model"}', { status: 400 }),
    ));

    const error = await collect(new OpenAICompatibleProvider().stream(request, context))
      .catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(AgentError);
    expect((error as AgentError).category).toBe("provider_http_error");
    expect((error as AgentError).status).toBe(400);
    expect((error as AgentError).message).toContain("400");
    expect((error as AgentError).message).not.toContain("secret-test-key");
  });

  it("畸形 JSON 载荷抛出 provider_protocol_error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(
      "data: {not-json\n\n",
    )));

    const error = await collect(new OpenAICompatibleProvider().stream(request, context))
      .catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(AgentError);
    expect((error as AgentError).category).toBe("provider_protocol_error");
  });

  it("请求被取消时抛出 aborted", async () => {
    const fetchMock = vi.fn().mockImplementation((_url: string, init: RequestInit) =>
      new Promise((_resolve, reject) => {
        init.signal?.addEventListener("abort", () => {
          reject(new DOMException("The operation was aborted.", "AbortError"));
        });
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const controller = new AbortController();
    const pending = collect(new OpenAICompatibleProvider().stream(request, {
      ...context,
      signal: controller.signal,
    }));
    controller.abort();

    const error = await pending.catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(AgentError);
    expect((error as AgentError).category).toBe("aborted");
  });

  it("助手历史消息中的工具调用会转换为厂商格式", async () => {
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(
      data({ choices: [{ delta: { content: "done" }, finish_reason: "stop" }] }),
      "data: [DONE]\n\n",
    ));
    vi.stubGlobal("fetch", fetchMock);

    const historyRequest: ModelRequest = {
      ...request,
      messages: [
        { role: "system", content: "sp" },
        { role: "user", content: "q" },
        {
          role: "assistant",
          content: "",
          toolCalls: [{ id: "call_1", name: "search_rules", arguments: '{"query":"x"}' }],
        },
        { role: "tool", toolCallId: "call_1", toolName: "search_rules", content: "结果", isError: false },
      ],
    };
    await collect(new OpenAICompatibleProvider().stream(historyRequest, context));

    const body = JSON.parse((fetchMock.mock.calls[0] as [string, RequestInit])[1].body as string);
    expect(body.messages[2]).toEqual({
      role: "assistant",
      content: "",
      tool_calls: [{
        id: "call_1",
        type: "function",
        function: { name: "search_rules", arguments: '{"query":"x"}' },
      }],
    });
    expect(body.messages[3]).toEqual({ role: "tool", tool_call_id: "call_1", content: "结果" });
  });

  it("tool_calls 乱序到达（index=1 先于 index=0）时仍按 index 升序发出 tool_call_end", async () => {
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(
      // index=1 的分片先到达
      data({ choices: [{ delta: { tool_calls: [
        { index: 1, id: "call_b", function: { name: "read_rules", arguments: '{"ids":["a"]}' } },
      ] } }] }),
      // index=0 的分片后到达
      data({ choices: [{ delta: { tool_calls: [
        { index: 0, id: "call_a", function: { name: "search_rules", arguments: '{"query":"x"}' } },
      ] } }] }),
      data({ choices: [{ delta: {}, finish_reason: "tool_calls" }] }),
      "data: [DONE]\n\n",
    ));
    vi.stubGlobal("fetch", fetchMock);

    const events = await collect(new OpenAICompatibleProvider().stream(request, context));

    const endEvents = events.filter((event) => event.type === "tool_call_end");
    expect(endEvents.map((event) => event.type === "tool_call_end" && event.index)).toEqual([0, 1]);
    expect(endEvents[0]).toMatchObject({ index: 0, id: "call_a", name: "search_rules" });
    expect(endEvents[1]).toMatchObject({ index: 1, id: "call_b", name: "read_rules" });
  });

  describe("API Key 脱敏", () => {
    const secret = "sk-supersecret-1234567890";
    const secretContext: ProviderContext = { ...context, apiKey: secret };

    it("HTTP 错误正文中回显的 API Key 会被替换为 [REDACTED]", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
        `{"error":"invalid auth for key ${secret}"}`,
        { status: 401 },
      )));

      const error = await collect(new OpenAICompatibleProvider().stream(request, secretContext))
        .catch((caught: unknown) => caught) as AgentError;

      expect(error).toBeInstanceOf(AgentError);
      expect(error.category).toBe("provider_http_error");
      expect(error.message).not.toContain(secret);
      expect(error.message).toContain(REDACTED);
      expect(error.message).toContain("401");
    });

    it("畸形 SSE payload 中回显的 API Key 会被替换为 [REDACTED]", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(
        `data: {broken ${secret}\n\n`,
      )));

      const error = await collect(new OpenAICompatibleProvider().stream(request, secretContext))
        .catch((caught: unknown) => caught) as AgentError;

      expect(error).toBeInstanceOf(AgentError);
      expect(error.category).toBe("provider_protocol_error");
      expect(error.message).not.toContain(secret);
      expect(error.message).toContain(REDACTED);
    });

    it("底层连接异常 message 中的 API Key 会被替换为 [REDACTED]", async () => {
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(
        new Error(`connect ECONNREFUSED while using key ${secret}`),
      ));

      const error = await collect(new OpenAICompatibleProvider().stream(request, secretContext))
        .catch((caught: unknown) => caught) as AgentError;

      expect(error).toBeInstanceOf(AgentError);
      expect(error.category).toBe("provider_http_error");
      expect(error.message).not.toContain(secret);
      expect(error.message).toContain(REDACTED);
      // 保留有用诊断信息
      expect(error.message).toContain("ECONNREFUSED");
      // 整个错误链都不含原始密钥：底层 cause.message 也已安全化
      const cause = error.cause as Error;
      expect(cause).toBeInstanceOf(Error);
      expect(cause.message).not.toContain(secret);
      expect(cause.message).toContain(REDACTED);
    });

    it("即使 API Key 极短（2 字符），Provider 统一兜底仍会脱敏", async () => {
      const shortKey = "ab";
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
        `{"error":"bad token ab in header"}`,
        { status: 403 },
      )));

      const error = await collect(new OpenAICompatibleProvider().stream(request, {
        ...context,
        apiKey: shortKey,
      })).catch((caught: unknown) => caught) as AgentError;

      expect(error).toBeInstanceOf(AgentError);
      expect(error.category).toBe("provider_http_error");
      expect(error.message).toContain(REDACTED);
      expect(error.message).toContain("403");
      // 原始短密钥子串不应以孤立形式残留
      expect(error.message).not.toContain(" ab ");
    });

    it("读取流式响应失败时 message 中的 API Key 会被替换为 [REDACTED]", async () => {
      const failingStream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.error(new Error(`stream broke with token ${secret}`));
        },
      });
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
        new Response(failingStream, { status: 200 }),
      ));

      const error = await collect(new OpenAICompatibleProvider().stream(request, secretContext))
        .catch((caught: unknown) => caught) as AgentError;

      expect(error).toBeInstanceOf(AgentError);
      expect(error.category).toBe("provider_protocol_error");
      expect(error.message).not.toContain(secret);
      expect(error.message).toContain(REDACTED);
    });
  });
});
