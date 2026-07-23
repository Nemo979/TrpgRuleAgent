import type { AddressInfo } from "node:net";
import { afterEach, describe, expect, it } from "vitest";
import {
  AgentError,
  REDACTED,
  type AgentEvent,
  type AgentMessage,
  type AgentRunContext,
} from "@trpg-rule-agent/agent";
import type { GatewayConfig } from "../src/config.ts";
import { createGatewayServer } from "../src/server.ts";
import type { SessionConnectionConfig } from "../src/session-config.ts";
import { GatewayService, type AgentFactory, type GatewayAgent } from "../src/service.ts";
import { InMemorySessionStore } from "../src/session-store.ts";

// ---------- 测试基础设施 ----------

function testConfig(overrides: Partial<GatewayConfig> = {}): GatewayConfig {
  return {
    host: "127.0.0.1",
    port: 0,
    sessionTtlMs: 60_000,
    allowedOrigins: ["https://app.example"],
    modelBaseUrlAllowlist: ["https://llm.example/v1", "https://alt.example"],
    allowLocalhostModel: false,
    maxBodyBytes: 16 * 1024,
    maxInputChars: 8000,
    rateLimitMaxRequests: 120,
    rateLimitWindowMs: 60_000,
    retrievalBaseUrl: "http://127.0.0.1:8765",
    allowedRulesets: ["pathfinder-1e", "demo"],
    defaultRulesetId: "pathfinder-1e",
    ...overrides,
  };
}

function sessionBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    provider: "openai-compatible",
    model: { id: "test-model" },
    baseUrl: "https://llm.example/v1",
    ...overrides,
  };
}

interface FakeCall {
  config: SessionConnectionConfig;
  initialMessages: AgentMessage[] | undefined;
  input?: string;
  apiKey?: string;
  aborted?: boolean;
}

interface FakeFactoryOptions {
  /** 每次 turn 的脚本事件（不含 turn_start，由 fake 自动发出）。 */
  scripts?: AgentEvent[][];
  /** 完成前等待此 Promise（并发测试用）。 */
  gate?: Promise<void>;
  /** 只发 turn_start，然后等待 signal abort（断连测试用）。 */
  waitForAbort?: boolean;
}

/** 模拟 RuleAgentRuntime 语义：成功累积消息，error 事件表示已回滚。 */
function fakeFactory(options: FakeFactoryOptions = {}): { factory: AgentFactory; calls: FakeCall[] } {
  const calls: FakeCall[] = [];
  let turn = 0;

  const factory: AgentFactory = (config, initialMessages) => {
    const call: FakeCall = { config, initialMessages };
    calls.push(call);
    const script = options.scripts?.[Math.min(turn, (options.scripts?.length ?? 1) - 1)] ?? [
      { type: "text_delta", delta: "答案" },
      { type: "turn_end" },
    ];
    turn += 1;

    const baseMessages: AgentMessage[] =
      initialMessages && initialMessages.length > 0
        ? [...initialMessages]
        : [{ role: "system", content: "系统提示" }];
    let finalMessages = baseMessages;

    const agent: GatewayAgent = {
      async *run(input: string, context: AgentRunContext): AsyncIterable<AgentEvent> {
        call.input = input;
        call.apiKey = context.apiKey;
        yield { type: "turn_start" };

        if (options.waitForAbort) {
          await new Promise<void>((resolve) => {
            if (context.signal?.aborted) {
              resolve();
              return;
            }
            context.signal?.addEventListener("abort", () => resolve(), { once: true });
          });
          call.aborted = true;
          yield { type: "error", error: new AgentError("aborted", "已取消") };
          return;
        }

        if (options.gate) {
          await options.gate;
        }

        let terminal = false;
        for (const event of script) {
          if (event.type === "error") {
            terminal = true;
          }
          yield event;
        }
        if (!terminal) {
          // 成功：模拟 runtime 累积本轮消息。
          finalMessages = [
            ...baseMessages,
            { role: "user", content: input },
            { role: "assistant", content: "答案", toolCalls: [] },
          ];
        }
      },
      resetTurnState() {},
      messages: () => [...finalMessages],
      sources: () => [
        { label: "S1", documentId: "doc-charge", fullPath: "战斗 > 冲锋", title: "冲锋" },
      ],
    };
    return agent;
  };

  return { factory, calls };
}

interface TestServer {
  baseUrl: string;
  sessions: InMemorySessionStore;
  close(): Promise<void>;
}

const servers: TestServer[] = [];

async function startGateway(
  factory: AgentFactory,
  configOverrides: Partial<GatewayConfig> = {},
): Promise<TestServer> {
  const config = testConfig(configOverrides);
  const sessions = new InMemorySessionStore({ ttlMs: config.sessionTtlMs });
  const service = new GatewayService({ config, sessions, createAgent: factory });
  const server = createGatewayServer({
    service,
    allowedOrigins: config.allowedOrigins,
    rateLimitMaxRequests: config.rateLimitMaxRequests,
    rateLimitWindowMs: config.rateLimitWindowMs,
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  const handle: TestServer = {
    baseUrl: `http://127.0.0.1:${port}`,
    sessions,
    close: () => new Promise((resolve) => server.close(() => resolve())),
  };
  servers.push(handle);
  return handle;
}

afterEach(async () => {
  await Promise.all(servers.splice(0).map((server) => server.close()));
});

function postSession(baseUrl: string, body: unknown): Promise<Response> {
  return fetch(`${baseUrl}/v1/sessions`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function createSession(
  baseUrl: string,
  body: Record<string, unknown> = sessionBody(),
): Promise<string> {
  const res = await postSession(baseUrl, body);
  expect(res.status).toBe(201);
  const parsed = (await res.json()) as { data: { sessionToken: string } };
  return parsed.data.sessionToken;
}

function turnRequest(
  baseUrl: string,
  token: string | undefined,
  apiKey: string | undefined,
  body: unknown,
  query = "",
): Promise<Response> {
  return fetch(`${baseUrl}/v1/turns${query}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(apiKey ? { "x-model-api-key": apiKey } : {}),
    },
    body: JSON.stringify(body),
  });
}

function parseSse(text: string): Array<Record<string, unknown>> {
  return text
    .split("\n\n")
    .filter((frame) => frame.startsWith("data: "))
    .map((frame) => JSON.parse(frame.slice("data: ".length)) as Record<string, unknown>);
}

async function errorCode(res: Response): Promise<string> {
  const body = (await res.json()) as { error: { code: string } };
  return body.error.code;
}

// ---------- 测试 ----------

describe("Gateway HTTP", () => {
  it("GET /health 返回 ok 并带安全响应头", async () => {
    const { factory } = fakeFactory();
    const { baseUrl } = await startGateway(factory);

    const res = await fetch(`${baseUrl}/health`);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ data: { status: "ok" } });
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(res.headers.get("referrer-policy")).toBe("no-referrer");
  });

  it("POST /v1/sessions：合法配置返回一次性 token，服务端只存摘要", async () => {
    const { factory } = fakeFactory();
    const { baseUrl, sessions } = await startGateway(factory);

    const res = await postSession(baseUrl, sessionBody({ rulesetId: "demo" }));
    expect(res.status).toBe(201);
    const body = (await res.json()) as { data: { sessionToken: string; expiresAt: string } };
    expect(body.data.sessionToken.length).toBeGreaterThanOrEqual(43);
    expect(Number.isNaN(Date.parse(body.data.expiresAt))).toBe(false);
    expect(sessions.keys().every((key) => /^[0-9a-f]{64}$/.test(key))).toBe(true);
  });

  it("POST /v1/sessions：空 body、缺模型、未知 provider、未知字段、retrievalBaseUrl 一律 400", async () => {
    const { factory } = fakeFactory();
    const { baseUrl } = await startGateway(factory);

    for (const body of [
      {},
      { provider: "openai-compatible" },
      sessionBody({ provider: "not-registered" }),
      sessionBody({ extra: 1 }),
      sessionBody({ model: { id: "m", temperature: 1 } }),
      sessionBody({ retrievalBaseUrl: "http://attacker.example" }),
    ]) {
      const res = await postSession(baseUrl, body);
      expect(res.status).toBe(400);
      expect(await errorCode(res)).toBe("invalid_request");
    }

    const badRuleset = await postSession(baseUrl, sessionBody({ rulesetId: "dnd-5e" }));
    expect(badRuleset.status).toBe(400);
    expect(await errorCode(badRuleset)).toBe("ruleset_not_allowed");
  });

  it("POST /v1/sessions 拒绝任何凭据字段（含嵌套）", async () => {
    const { factory } = fakeFactory();
    const { baseUrl } = await startGateway(factory);

    for (const body of [
      sessionBody({ apiKey: "sk-x" }),
      sessionBody({ api_key: "sk-x" }),
      sessionBody({ model: { id: "m", token: "t" } }),
      { ...sessionBody(), nested: { secret: "s" } },
    ]) {
      const res = await postSession(baseUrl, body);
      expect(res.status).toBe(400);
      expect(await errorCode(res)).toBe("credentials_not_allowed");
    }
  });

  it("POST /v1/sessions：baseUrl 不在白名单/路径越界在创建时即拒绝", async () => {
    const { factory } = fakeFactory();
    const { baseUrl } = await startGateway(factory);

    const forbidden = await postSession(baseUrl, sessionBody({ baseUrl: "https://evil.example/v1" }));
    expect(forbidden.status).toBe(403);
    expect(await errorCode(forbidden)).toBe("model_base_url_forbidden");

    // 白名单条目是 https://llm.example/v1：兄弟路径越界。
    const escape = await postSession(baseUrl, sessionBody({ baseUrl: "https://llm.example/admin" }));
    expect(escape.status).toBe(403);

    const http = await postSession(baseUrl, sessionBody({ baseUrl: "http://llm.example/v1" }));
    expect(http.status).toBe(400);
  });

  it("turn 使用会话自己的连接配置创建 Agent（BYOK 自定义模型）", async () => {
    const { factory, calls } = fakeFactory();
    const { baseUrl } = await startGateway(factory);
    const token = await createSession(
      baseUrl,
      sessionBody({
        baseUrl: "https://alt.example/custom/v1",
        model: { id: "my-model", maxTokens: 2048, reasoning: true },
        rulesetId: "demo",
      }),
    );

    const res = await turnRequest(baseUrl, token, "sk-user", { input: "问题" });
    expect(res.status).toBe(200);
    await res.text();

    expect(calls[0]?.config).toEqual({
      provider: "openai-compatible",
      model: { id: "my-model", maxTokens: 2048, reasoning: true },
      baseUrl: "https://alt.example/custom/v1",
      rulesetId: "demo",
    });
    expect(calls[0]?.apiKey).toBe("sk-user");
  });

  it("turn 正常流：SSE 事件序列 + Key 只进入 per-turn context + sources 含 documentId", async () => {
    const { factory, calls } = fakeFactory();
    const { baseUrl } = await startGateway(factory);
    const token = await createSession(baseUrl);

    const res = await turnRequest(baseUrl, token, "sk-test-key", { input: "冲锋能否与借机攻击叠加？" });
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/event-stream");
    expect(res.headers.get("cache-control")).toBe("no-store");

    const text = await res.text();
    const events = parseSse(text);
    expect(events.map((event) => event.type)).toEqual([
      "turn_start", "text_delta", "turn_end", "sources", "done",
    ]);
    expect(calls[0]?.apiKey).toBe("sk-test-key");
    expect(calls[0]?.input).toContain("冲锋");
    expect(text).not.toContain("sk-test-key");
    const sources = events.find((event) => event.type === "sources");
    expect(sources?.sources).toEqual([
      { label: "S1", documentId: "doc-charge", fullPath: "战斗 > 冲锋", title: "冲锋" },
    ]);
  });

  it("turn 鉴权：缺 token=401、伪造 token=401、缺 X-Model-Api-Key=400", async () => {
    const { factory } = fakeFactory();
    const { baseUrl } = await startGateway(factory);
    const token = await createSession(baseUrl);

    expect((await turnRequest(baseUrl, undefined, "sk", { input: "q" })).status).toBe(401);
    expect((await turnRequest(baseUrl, "forged", "sk", { input: "q" })).status).toBe(401);
    expect((await turnRequest(baseUrl, token, undefined, { input: "q" })).status).toBe(400);
  });

  it("turn 拒绝 body/query 中的凭据与未知字段", async () => {
    const { factory, calls } = fakeFactory();
    const { baseUrl } = await startGateway(factory);
    const token = await createSession(baseUrl);

    const inBody = await turnRequest(baseUrl, token, "sk", { input: "q", apiKey: "sk-leak" });
    expect(inBody.status).toBe(400);
    expect(await errorCode(inBody)).toBe("credentials_not_allowed");

    const inQuery = await turnRequest(baseUrl, token, "sk", { input: "q" }, "?api_key=sk-leak");
    expect(inQuery.status).toBe(400);

    const unknownField = await turnRequest(baseUrl, token, "sk", { input: "q", extra: 1 });
    expect(unknownField.status).toBe(400);
    expect(await errorCode(unknownField)).toBe("invalid_request");

    expect(calls).toHaveLength(0);
  });

  it("HTTP 限制：超长 input=400、超大 body=413、错误 Content-Type=415、非法 JSON=400", async () => {
    const { factory } = fakeFactory();
    const { baseUrl } = await startGateway(factory);
    const token = await createSession(baseUrl);

    const longInput = await turnRequest(baseUrl, token, "sk", { input: "字".repeat(8001) });
    expect(longInput.status).toBe(413); // 8001 个多字节字符 > 16KiB，先触发体积上限

    const shortButOverLimit = await turnRequest(baseUrl, token, "sk", { input: "a".repeat(8001) });
    expect(shortButOverLimit.status).toBe(400);
    expect(await errorCode(shortButOverLimit)).toBe("input_too_long");

    const bigBody = await fetch(`${baseUrl}/v1/turns`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${token}`,
        "x-model-api-key": "sk",
      },
      body: JSON.stringify({ input: "q", pad: "x".repeat(17 * 1024) }),
    });
    expect(bigBody.status).toBe(413);

    const wrongType = await fetch(`${baseUrl}/v1/turns`, {
      method: "POST",
      headers: { "content-type": "text/plain", authorization: `Bearer ${token}`, "x-model-api-key": "sk" },
      body: "input=q",
    });
    expect(wrongType.status).toBe(415);

    const badJson = await fetch(`${baseUrl}/v1/turns`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${token}`, "x-model-api-key": "sk" },
      body: "{broken",
    });
    expect(badJson.status).toBe(400);
  });

  it("CORS：白名单 Origin 回显，非白名单不回显，预检 204", async () => {
    const { factory } = fakeFactory();
    const { baseUrl } = await startGateway(factory);

    const preflight = await fetch(`${baseUrl}/v1/turns`, {
      method: "OPTIONS",
      headers: { origin: "https://app.example" },
    });
    expect(preflight.status).toBe(204);
    expect(preflight.headers.get("access-control-allow-origin")).toBe("https://app.example");
    expect(preflight.headers.get("access-control-allow-headers")).toContain("X-Model-Api-Key");

    const denied = await fetch(`${baseUrl}/v1/turns`, {
      method: "OPTIONS",
      headers: { origin: "https://evil.example" },
    });
    expect(denied.headers.get("access-control-allow-origin")).toBeNull();

    const health = await fetch(`${baseUrl}/health`, { headers: { origin: "https://app.example" } });
    expect(health.headers.get("access-control-allow-origin")).toBe("https://app.example");
  });

  it("SSE 错误事件只含 category/message：无 cause、stack、API Key", async () => {
    const boom = new AgentError("provider_http_error", "模型服务返回 HTTP 500", {
      status: 500,
      cause: new Error("connect failed with sk-live-12345"),
    });
    const { factory } = fakeFactory({ scripts: [[{ type: "error", error: boom }]] });
    const { baseUrl } = await startGateway(factory);
    const token = await createSession(baseUrl);

    const res = await turnRequest(baseUrl, token, "sk-live-12345", { input: "q" });
    const text = await res.text();
    const events = parseSse(text);

    expect(events.map((event) => event.type)).toEqual(["turn_start", "error", "done"]);
    const errorEvent = events.find((event) => event.type === "error") as {
      error: Record<string, unknown>;
    };
    expect(Object.keys(errorEvent.error).sort()).toEqual(["category", "message"]);
    expect(text).not.toContain("sk-live-12345");
    expect(text).not.toContain("cause");
    expect(text).not.toContain("stack");
  });

  it("SSE 末端脱敏：顶层 error.message 直接含长 Key（自定义 Provider 未脱敏）也被 [REDACTED]", async () => {
    // 模拟可扩展的自定义 Provider：未调用 redactAgentError，把本次 apiKey
    // 直接拼进顶层 error.message（而非仅 cause）。Gateway 出口必须兜底。
    const longKey = "sk-live-abcdefghijklmnopqrstuvwxyz-0123456789";
    const leak = new AgentError("provider_http_error", `请求失败，使用的 Key=${longKey} 无效`);
    const { factory } = fakeFactory({ scripts: [[{ type: "error", error: leak }]] });
    const { baseUrl } = await startGateway(factory);
    const token = await createSession(baseUrl);

    const res = await turnRequest(baseUrl, token, longKey, { input: "q" });
    const text = await res.text();
    const errorEvent = parseSse(text).find((event) => event.type === "error") as {
      error: { category: string; message: string };
    };

    expect(text).not.toContain(longKey);
    expect(errorEvent.error.message).toContain(REDACTED);
    expect(errorEvent.error.message).not.toContain(longKey);
    expect(text).not.toContain("cause");
    expect(text).not.toContain("stack");
  });

  it("SSE 末端脱敏：2 字符短 Key 直接出现在 error.message 中也被 [REDACTED]", async () => {
    const shortKey = "ab";
    const leak = new AgentError("provider_http_error", `bad key ${shortKey} here`);
    const { factory } = fakeFactory({ scripts: [[{ type: "error", error: leak }]] });
    const { baseUrl } = await startGateway(factory);
    const token = await createSession(baseUrl);

    const res = await turnRequest(baseUrl, token, shortKey, { input: "q" });
    const text = await res.text();
    const errorEvent = parseSse(text).find((event) => event.type === "error") as {
      error: { message: string };
    };

    expect(errorEvent.error.message).toBe(`bad key ${REDACTED} here`);
  });

  it("SSE 末端脱敏：tool_error.message 直接含 Key 也被 [REDACTED]，且不回滚历史", async () => {
    const key = "sk-tool-leak-9999";
    const { factory } = fakeFactory({
      scripts: [
        [
          { type: "tool_start", toolCallId: "t1", toolName: "search_rules", arguments: {} },
          {
            type: "tool_error",
            toolCallId: "t1",
            toolName: "search_rules",
            error: new AgentError("tool_execution_error", `工具失败，key=${key}`),
          },
          { type: "text_delta", delta: "已恢复" },
          { type: "turn_end" },
        ],
      ],
    });
    const { baseUrl } = await startGateway(factory);
    const token = await createSession(baseUrl);

    const res = await turnRequest(baseUrl, token, key, { input: "q" });
    const text = await res.text();
    const events = parseSse(text);
    const toolError = events.find((event) => event.type === "tool_error") as {
      error: { category: string; message: string };
    };

    expect(text).not.toContain(key);
    expect(toolError.error.message).toContain(REDACTED);
    expect(Object.keys(toolError.error).sort()).toEqual(["category", "message"]);
    // tool_error 非终止性：turn 仍成功，正常发出 sources 与 done。
    expect(events.map((event) => event.type)).toContain("sources");
  });

  it("同一会话并发 turn：第二个请求 409", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const { factory } = fakeFactory({ gate });
    const { baseUrl } = await startGateway(factory);
    const token = await createSession(baseUrl);

    const first = turnRequest(baseUrl, token, "sk", { input: "q1" });
    // 等第一个请求进入处理（beginTurn 已占用）。
    await new Promise((resolve) => setTimeout(resolve, 100));
    const second = await turnRequest(baseUrl, token, "sk", { input: "q2" });
    expect(second.status).toBe(409);

    release();
    const firstRes = await first;
    expect(firstRes.status).toBe(200);
    await firstRes.text();

    // turn 结束后可以再次发起。
    const third = await turnRequest(baseUrl, token, "sk", { input: "q3" });
    expect(third.status).toBe(200);
    await third.text();
  });

  it("成功 turn 写回会话历史；下一个 turn 收到 initialMessages", async () => {
    const { factory, calls } = fakeFactory();
    const { baseUrl } = await startGateway(factory);
    const token = await createSession(baseUrl);

    await (await turnRequest(baseUrl, token, "sk", { input: "第一问" })).text();
    expect(calls[0]?.initialMessages).toBeUndefined();

    await (await turnRequest(baseUrl, token, "sk", { input: "第二问" })).text();
    // system + user + assistant 来自第一轮。
    expect(calls[1]?.initialMessages).toHaveLength(3);
    expect(calls[1]?.initialMessages?.some((m) => m.role === "user" && m.content === "第一问")).toBe(true);
  });

  it("失败 turn 不污染会话历史", async () => {
    const failure = new AgentError("provider_http_error", "HTTP 500");
    const { factory, calls } = fakeFactory({
      scripts: [[{ type: "error", error: failure }], [{ type: "text_delta", delta: "ok" }, { type: "turn_end" }]],
    });
    const { baseUrl } = await startGateway(factory);
    const token = await createSession(baseUrl);

    await (await turnRequest(baseUrl, token, "sk", { input: "失败的一问" })).text();
    await (await turnRequest(baseUrl, token, "sk", { input: "第二问" })).text();

    // 第一轮失败未写回，第二轮拿不到任何历史。
    expect(calls[1]?.initialMessages).toBeUndefined();
  });

  it("DELETE /v1/session：删除后 turn 401，重复删除 404", async () => {
    const { factory } = fakeFactory();
    const { baseUrl } = await startGateway(factory);
    const token = await createSession(baseUrl);

    const del = await fetch(`${baseUrl}/v1/session`, {
      method: "DELETE",
      headers: { authorization: `Bearer ${token}` },
    });
    expect(del.status).toBe(204);

    expect((await turnRequest(baseUrl, token, "sk", { input: "q" })).status).toBe(401);

    const again = await fetch(`${baseUrl}/v1/session`, {
      method: "DELETE",
      headers: { authorization: `Bearer ${token}` },
    });
    expect(again.status).toBe(404);
  });

  it("客户端断开连接触发 AbortSignal", async () => {
    const { factory, calls } = fakeFactory({ waitForAbort: true });
    const { baseUrl } = await startGateway(factory);
    const token = await createSession(baseUrl);

    const controller = new AbortController();
    const res = await fetch(`${baseUrl}/v1/turns`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${token}`,
        "x-model-api-key": "sk",
      },
      body: JSON.stringify({ input: "q" }),
      signal: controller.signal,
    });
    // 读到第一个事件（turn_start）后断开。
    const reader = res.body!.getReader();
    await reader.read();
    controller.abort();

    // 服务端应观察到 abort 并结束本轮。
    await expect
      .poll(() => calls[0]?.aborted, { timeout: 3000 })
      .toBe(true);
  });
});
