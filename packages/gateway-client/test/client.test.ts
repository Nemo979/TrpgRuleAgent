import { describe, expect, it } from "vitest";
import { TextEncoder } from "node:util";
import { GatewayClient } from "../src/client.ts";
import type { GatewayEvent } from "../src/protocol.ts";
import { TransportError } from "../src/transport.ts";
import type {
  GatewayTransport,
  TransportRequest,
  TransportResponse,
  TransportStreamResponse,
} from "../src/transport.ts";

const encoder = new TextEncoder();

const SESSION_TOKEN = "tok-1";
const EXPIRES = "2030-01-01T00:00:00.000Z";

// ---------- 测试基础设施 ----------

type RequestPlan = (req: TransportRequest) => TransportResponse | Promise<TransportResponse>;
type StreamPlan = (req: TransportRequest) => TransportStreamResponse | Promise<TransportStreamResponse>;

class MockTransport implements GatewayTransport {
  readonly requests: TransportRequest[] = [];
  readonly streams: TransportRequest[] = [];

  constructor(
    private readonly requestPlan: RequestPlan,
    private readonly streamPlan: StreamPlan,
  ) {}

  async request(req: TransportRequest): Promise<TransportResponse> {
    this.requests.push(req);
    return await this.requestPlan(req);
  }

  async stream(req: TransportRequest): Promise<TransportStreamResponse> {
    this.streams.push(req);
    return await this.streamPlan(req);
  }
}

function sseFrame(obj: unknown): string {
  return `data: ${JSON.stringify(obj)}\n\n`;
}

async function* sseBody(events: Array<Record<string, unknown>>): AsyncIterable<Uint8Array> {
  for (const event of events) {
    yield encoder.encode(sseFrame(event));
  }
}

function emptyBody(): AsyncIterable<Uint8Array> {
  return (async function* (): AsyncIterable<Uint8Array> {})();
}

function streamResponse(
  status: number,
  events: Array<Record<string, unknown>>,
  errorText = "",
): TransportStreamResponse {
  return {
    status,
    contentType: "text/event-stream",
    body: sseBody(events),
    readText: async () => errorText,
  };
}

async function collect<T>(gen: AsyncIterable<T>): Promise<T[]> {
  const out: T[] = [];
  for await (const item of gen) {
    out.push(item);
  }
  return out;
}

function sessionPlan(_req: TransportRequest): TransportResponse {
  return {
    status: 201,
    text: JSON.stringify({ data: { sessionToken: SESSION_TOKEN, expiresAt: EXPIRES } }),
  };
}

const FAKE_API_KEY = "sk-fake-never-stored-12345";

// ---------- createSession ----------

describe("GatewayClient.createSession", () => {
  it("成功返回安全公开快照（不含 sessionToken），connected 变 true", async () => {
    const transport = new MockTransport(sessionPlan, () => {
      throw new Error("stream 不应被调用");
    });
    const client = new GatewayClient({ transport });
    const res = await client.createSession({
      provider: "p",
      model: { id: "m" },
      baseUrl: "https://llm.example/v1",
    });

    // 公开返回值 = 安全快照：不含 sessionToken 字段/值。
    expect(res).toEqual({ connected: true, turnInFlight: false, expiresAt: EXPIRES });
    expect("sessionToken" in res).toBe(false);
    expect(JSON.stringify(res)).not.toContain(SESSION_TOKEN);
    expect(client.connected).toBe(true);
    expect(client.status()).toEqual({ connected: true, turnInFlight: false, expiresAt: EXPIRES });

    const serializedClient = JSON.stringify(client);
    const serializedStatus = JSON.stringify(client.status());
    expect(serializedClient).not.toContain(SESSION_TOKEN);
    expect(serializedStatus).not.toContain(SESSION_TOKEN);
    // 即便从未传入，也不应出现 apiKey 字样（回归防泄漏）。
    expect(serializedClient).not.toContain(FAKE_API_KEY);
    expect(serializedStatus).not.toContain(FAKE_API_KEY);
  });

  it("请求体含 provider/model/baseUrl、透传 rulesetId，但不含凭据头", async () => {
    const transport = new MockTransport(sessionPlan, () => {
      throw new Error("no");
    });
    const client = new GatewayClient({ transport });
    await client.createSession({
      provider: "p1",
      model: { id: "m1", contextWindow: 8000, maxTokens: 2000, reasoning: true },
      baseUrl: "https://llm.example/v1",
      rulesetId: "demo",
    });

    const req = transport.requests[0]!;
    expect(req.method).toBe("POST");
    expect(req.path).toBe("/v1/sessions");
    expect(req.headers?.["Content-Type"]).toBe("application/json");
    expect(req.headers?.["X-Model-Api-Key"]).toBeUndefined();

    const body = JSON.parse(req.body ?? "{}");
    expect(body.provider).toBe("p1");
    expect(body.model).toEqual({ id: "m1", contextWindow: 8000, maxTokens: 2000, reasoning: true });
    expect(body.baseUrl).toBe("https://llm.example/v1");
    expect(body.rulesetId).toBe("demo");
    expect(body).not.toHaveProperty("apiKey");
  });
});

// ---------- runTurn ----------

describe("GatewayClient.runTurn", () => {
  it("脚本化 SSE 迭代产出 typed 事件，done 后结束，请求头含鉴权与 apiKey", async () => {
    const events: Array<Record<string, unknown>> = [
      { type: "turn_start" },
      { type: "text_delta", delta: "你好" },
      { type: "turn_end" },
      { type: "sources", sources: [{ label: "L", documentId: "d", fullPath: "p" }] },
      { type: "done" },
    ];
    const transport = new MockTransport(sessionPlan, () => streamResponse(200, events));
    const client = new GatewayClient({ transport });
    await client.createSession({ provider: "p", model: { id: "m" }, baseUrl: "https://llm.example/v1" });

    const collected = await collect(client.runTurn("hello", { apiKey: "sk-1" }));
    expect(collected.map((e) => e.type)).toEqual([
      "turn_start",
      "text_delta",
      "turn_end",
      "sources",
      "done",
    ]);

    const req = transport.streams[0]!;
    expect(req.headers?.["Authorization"]).toBe(`Bearer ${SESSION_TOKEN}`);
    expect(req.headers?.["X-Model-Api-Key"]).toBe("sk-1");
    expect(JSON.parse(req.body ?? "{}")).toEqual({ input: "hello" });
    expect(client.turnInFlight).toBe(false);
    // apiKey 从不写入实例。
    expect(JSON.stringify(client)).not.toContain("sk-1");
  });

  it("未创建会话 -> invalid_state，不触发 stream", async () => {
    const transport = new MockTransport(
      () => {
        throw new Error("no");
      },
      () => {
        throw new Error("no");
      },
    );
    const client = new GatewayClient({ transport });
    await expect(collect(client.runTurn("q", { apiKey: "sk" }))).rejects.toMatchObject({
      code: "invalid_state",
    });
    expect(transport.streams).toHaveLength(0);
  });

  it("缺 apiKey（空串）-> invalid_state", async () => {
    const transport = new MockTransport(sessionPlan, () => {
      throw new Error("no");
    });
    const client = new GatewayClient({ transport });
    await client.createSession({ provider: "p", model: { id: "m" }, baseUrl: "https://llm.example/v1" });
    await expect(collect(client.runTurn("q", { apiKey: "" }))).rejects.toMatchObject({
      code: "invalid_state",
    });
  });

  it("并发防护：第一次迭代未结束时第二次立即抛 turn_in_flight", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    async function* gatedBody(): AsyncIterable<Uint8Array> {
      yield encoder.encode(sseFrame({ type: "turn_start" }));
      yield encoder.encode(sseFrame({ type: "text_delta", delta: "x" }));
      await gate;
      yield encoder.encode(sseFrame({ type: "turn_end" }));
      yield encoder.encode(sseFrame({ type: "done" }));
    }
    const transport = new MockTransport(sessionPlan, () => ({
      status: 200,
      contentType: "text/event-stream",
      body: gatedBody(),
      readText: async () => "",
    }));
    const client = new GatewayClient({ transport });
    await client.createSession({ provider: "p", model: { id: "m" }, baseUrl: "https://llm.example/v1" });

    const iter = client.runTurn("q", { apiKey: "sk" });
    const first = await iter.next();
    expect(first.value.type).toBe("turn_start");

    await expect(client.runTurn("q2", { apiKey: "sk" }).next()).rejects.toMatchObject({
      code: "turn_in_flight",
    });

    release();
    const rest: GatewayEvent[] = [];
    for await (const ev of iter) {
      rest.push(ev);
    }
    expect(rest.map((e) => e.type)).toEqual(["text_delta", "turn_end", "done"]);
    expect(client.turnInFlight).toBe(false);
  });

  it("HTTP 401 -> unauthorized", async () => {
    const transport = new MockTransport(sessionPlan, () => ({
      status: 401,
      contentType: "application/json",
      body: emptyBody(),
      readText: async () => JSON.stringify({ error: { code: "unauthorized", message: "无鉴权" } }),
    }));
    const client = new GatewayClient({ transport });
    await client.createSession({ provider: "p", model: { id: "m" }, baseUrl: "https://llm.example/v1" });
    await expect(collect(client.runTurn("q", { apiKey: "sk" }))).rejects.toMatchObject({
      code: "unauthorized",
    });
  });

  it("HTTP 409 -> turn_in_flight", async () => {
    const transport = new MockTransport(sessionPlan, () => ({
      status: 409,
      contentType: "application/json",
      body: emptyBody(),
      readText: async () => JSON.stringify({ error: { code: "turn_in_flight", message: "进行中" } }),
    }));
    const client = new GatewayClient({ transport });
    await client.createSession({ provider: "p", model: { id: "m" }, baseUrl: "https://llm.example/v1" });
    await expect(collect(client.runTurn("q", { apiKey: "sk" }))).rejects.toMatchObject({
      code: "turn_in_flight",
    });
  });

  it("错误体 code 为 turn_in_flight（即便非 409）-> turn_in_flight", async () => {
    const transport = new MockTransport(sessionPlan, () => ({
      status: 400,
      contentType: "application/json",
      body: emptyBody(),
      readText: async () => JSON.stringify({ error: { code: "turn_in_flight", message: "进行中" } }),
    }));
    const client = new GatewayClient({ transport });
    await client.createSession({ provider: "p", model: { id: "m" }, baseUrl: "https://llm.example/v1" });
    await expect(collect(client.runTurn("q", { apiKey: "sk" }))).rejects.toMatchObject({
      code: "turn_in_flight",
    });
  });

  it("其他非 2xx（500）-> http_error，带 gatewayCode/status", async () => {
    const transport = new MockTransport(sessionPlan, () => ({
      status: 500,
      contentType: "application/json",
      body: emptyBody(),
      readText: async () => JSON.stringify({ error: { code: "boom", message: "服务端错误" } }),
    }));
    const client = new GatewayClient({ transport });
    await client.createSession({ provider: "p", model: { id: "m" }, baseUrl: "https://llm.example/v1" });
    let err: unknown = undefined;
    try {
      await collect(client.runTurn("q", { apiKey: "sk" }));
    } catch (e) {
      err = e;
    }
    expect(err).toMatchObject({ code: "http_error", status: 500, gatewayCode: "boom" });
  });

  it("流内 error 事件被正常产出（不转成 throw），message 兜底脱敏", async () => {
    const events: Array<Record<string, unknown>> = [
      { type: "turn_start" },
      { type: "error", error: { category: "c", message: "fail key=sk-1" } },
      { type: "done" },
    ];
    const transport = new MockTransport(sessionPlan, () => streamResponse(200, events));
    const client = new GatewayClient({ transport });
    await client.createSession({ provider: "p", model: { id: "m" }, baseUrl: "https://llm.example/v1" });

    const collected = await collect(client.runTurn("q", { apiKey: "sk-1" }));
    const errorEvent = collected.find((e) => e.type === "error") as
      | Extract<GatewayEvent, { type: "error" }>
      | undefined;
    expect(errorEvent).toBeDefined();
    expect(errorEvent?.error.message).toContain("[REDACTED]");
    expect(errorEvent?.error.message).not.toContain("sk-1");
  });

  it("abort()：迭代中调用使 transport 收到 abort 信号，迭代抛 aborted 且 turnInFlight 归位", async () => {
    async function* abortableBody(req: TransportRequest): AsyncIterable<Uint8Array> {
      yield encoder.encode(sseFrame({ type: "turn_start" }));
      await new Promise<void>((resolve, reject) => {
        if (req.signal?.aborted) {
          reject(new TransportError("aborted", "已取消"));
          return;
        }
        req.signal?.addEventListener(
          "abort",
          () => reject(new TransportError("aborted", "已取消")),
          { once: true },
        );
      });
    }
    const transport = new MockTransport(sessionPlan, (req) => ({
      status: 200,
      contentType: "text/event-stream",
      body: abortableBody(req),
      readText: async () => "",
    }));
    const client = new GatewayClient({ transport });
    await client.createSession({ provider: "p", model: { id: "m" }, baseUrl: "https://llm.example/v1" });

    const iter = client.runTurn("q", { apiKey: "sk" });
    const first = await iter.next();
    expect(first.value.type).toBe("turn_start");

    client.abort();
    await expect(iter.next()).rejects.toMatchObject({ code: "aborted" });
    expect(client.turnInFlight).toBe(false);
  });

  it("成功状态但 Content-Type 不是 text/event-stream -> protocol_error", async () => {
    const transport = new MockTransport(sessionPlan, () => ({
      status: 200,
      contentType: "text/html; charset=utf-8",
      body: sseBody([{ type: "turn_start" }, { type: "done" }]),
      readText: async () => "",
    }));
    const client = new GatewayClient({ transport });
    await client.createSession({ provider: "p", model: { id: "m" }, baseUrl: "https://llm.example/v1" });
    await expect(collect(client.runTurn("q", { apiKey: "sk" }))).rejects.toMatchObject({
      code: "protocol_error",
    });
    expect(client.turnInFlight).toBe(false);
  });

  it("Content-Type 带参数（text/event-stream; charset=utf-8）被接受", async () => {
    const transport = new MockTransport(sessionPlan, () => ({
      status: 200,
      contentType: "text/event-stream; charset=utf-8",
      body: sseBody([{ type: "turn_start" }, { type: "turn_end" }, { type: "done" }]),
      readText: async () => "",
    }));
    const client = new GatewayClient({ transport });
    await client.createSession({ provider: "p", model: { id: "m" }, baseUrl: "https://llm.example/v1" });
    const collected = await collect(client.runTurn("q", { apiKey: "sk" }));
    expect(collected.map((e) => e.type)).toEqual(["turn_start", "turn_end", "done"]);
  });

  it("流在 done 之前提前结束 -> protocol_error，turnInFlight 归位", async () => {
    const transport = new MockTransport(sessionPlan, () =>
      streamResponse(200, [{ type: "turn_start" }, { type: "text_delta", delta: "半" }]),
    );
    const client = new GatewayClient({ transport });
    await client.createSession({ provider: "p", model: { id: "m" }, baseUrl: "https://llm.example/v1" });

    const received: string[] = [];
    let err: { code?: string } | undefined;
    try {
      for await (const ev of client.runTurn("q", { apiKey: "sk" })) {
        received.push(ev.type);
      }
    } catch (e) {
      err = e as { code?: string };
    }
    // 已到达的事件仍然产出，随后因截断抛 protocol_error。
    expect(received).toEqual(["turn_start", "text_delta"]);
    expect(err?.code).toBe("protocol_error");
    expect(client.turnInFlight).toBe(false);
  });

  it("空流（无任何事件即结束）-> protocol_error", async () => {
    const transport = new MockTransport(sessionPlan, () => ({
      status: 200,
      contentType: "text/event-stream",
      body: emptyBody(),
      readText: async () => "",
    }));
    const client = new GatewayClient({ transport });
    await client.createSession({ provider: "p", model: { id: "m" }, baseUrl: "https://llm.example/v1" });
    await expect(collect(client.runTurn("q", { apiKey: "sk" }))).rejects.toMatchObject({
      code: "protocol_error",
    });
  });
});

// ---------- 会话生命周期 ----------

describe("GatewayClient 会话生命周期", () => {
  it("deleteSession：best-effort，transport.request 抛错也不 throw，connected 变 false", async () => {
    const transport = new MockTransport(
      (req) => {
        if (req.method === "POST" && req.path === "/v1/sessions") {
          return { status: 201, text: JSON.stringify({ data: { sessionToken: SESSION_TOKEN, expiresAt: EXPIRES } }) };
        }
        // DELETE 路径抛网络错误。
        throw new Error("network down");
      },
      () => {
        throw new Error("no");
      },
    );
    const client = new GatewayClient({ transport });
    await client.createSession({ provider: "p", model: { id: "m" }, baseUrl: "https://llm.example/v1" });
    expect(client.connected).toBe(true);

    await expect(client.deleteSession()).resolves.toBeUndefined();
    expect(client.connected).toBe(false);
  });

  it("deleteSession：成功路径发起 DELETE 且清空会话", async () => {
    const transport = new MockTransport(
      (req) => {
        if (req.method === "POST" && req.path === "/v1/sessions") {
          return { status: 201, text: JSON.stringify({ data: { sessionToken: SESSION_TOKEN, expiresAt: EXPIRES } }) };
        }
        return { status: 204, text: "" };
      },
      () => {
        throw new Error("no");
      },
    );
    const client = new GatewayClient({ transport });
    await client.createSession({ provider: "p", model: { id: "m" }, baseUrl: "https://llm.example/v1" });
    await client.deleteSession();
    expect(client.connected).toBe(false);
    const del = transport.requests.find((r) => r.method === "DELETE");
    expect(del?.path).toBe("/v1/session");
    expect(del?.headers?.["Authorization"]).toBe(`Bearer ${SESSION_TOKEN}`);
  });

  it("disconnect()：本地断开，不发 DELETE 网络请求", async () => {
    const transport = new MockTransport(sessionPlan, () => {
      throw new Error("no");
    });
    const client = new GatewayClient({ transport });
    await client.createSession({ provider: "p", model: { id: "m" }, baseUrl: "https://llm.example/v1" });
    const before = transport.requests.length;
    client.disconnect();
    expect(client.connected).toBe(false);
    expect(transport.requests).toHaveLength(before);
    expect(transport.requests.some((r) => r.method === "DELETE")).toBe(false);
  });
});

// ---------- 安全：错误路径脱敏 ----------

describe("GatewayClient 安全：错误路径脱敏", () => {
  it("stream 返回 500 且 error message 含 apiKey -> 抛出 message 已脱敏为 [REDACTED]", async () => {
    const apiKey = "sk-secret-999";
    const transport = new MockTransport(sessionPlan, () => ({
      status: 500,
      contentType: "application/json",
      body: emptyBody(),
      readText: async () => JSON.stringify({ error: { code: "boom", message: `failed key=${apiKey}` } }),
    }));
    const client = new GatewayClient({ transport });
    await client.createSession({ provider: "p", model: { id: "m" }, baseUrl: "https://llm.example/v1" });

    let err: { code?: string; message?: string } | undefined;
    try {
      await collect(client.runTurn("q", { apiKey }));
    } catch (e) {
      err = e as { code?: string; message?: string };
    }
    expect(err?.code).toBe("http_error");
    expect(err?.message).toContain("[REDACTED]");
    expect(err?.message).not.toContain(apiKey);
  });
});
