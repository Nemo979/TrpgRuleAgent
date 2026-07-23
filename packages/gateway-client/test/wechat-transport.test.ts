import { describe, expect, it } from "vitest";
import { TextEncoder } from "node:util";
import { GatewayClient } from "../src/client.ts";
import { parseSseFrames } from "../src/sse.ts";
import { TransportError } from "../src/transport.ts";
import { WeChatTransport } from "../src/wechat-transport.ts";
import type {
  WeChatRequestParams,
  WeChatStreamParams,
} from "../src/wechat-transport.ts";
import type { GatewayEvent } from "../src/protocol.ts";

const encoder = new TextEncoder();

const BASE_URL = "https://gateway.example.com";
const API_KEY = "sk-super-secret-key";

// ---------- 测试基础设施 ----------

/** 记录并手动驱动普通请求的 fake 宿主适配器。 */
class FakeRequestHost {
  readonly calls: WeChatRequestParams[] = [];
  abortCount = 0;

  readonly adapter = (params: WeChatRequestParams) => {
    this.calls.push(params);
    return { abort: () => (this.abortCount += 1) };
  };

  get last(): WeChatRequestParams {
    const call = this.calls[this.calls.length - 1];
    if (!call) {
      throw new Error("尚未有请求");
    }
    return call;
  }
}

/** 记录并手动驱动流式请求的 fake 宿主适配器。 */
class FakeStreamHost {
  readonly calls: WeChatStreamParams[] = [];
  abortCount = 0;

  readonly adapter = (params: WeChatStreamParams) => {
    this.calls.push(params);
    return { abort: () => (this.abortCount += 1) };
  };

  get last(): WeChatStreamParams {
    const call = this.calls[this.calls.length - 1];
    if (!call) {
      throw new Error("尚未有流式请求");
    }
    return call;
  }
}

function makeTransport(reqHost: FakeRequestHost, streamHost: FakeStreamHost): WeChatTransport {
  return new WeChatTransport({
    baseUrl: `${BASE_URL}/`,
    request: reqHost.adapter,
    streamRequest: streamHost.adapter,
  });
}

async function collect<T>(gen: AsyncIterable<T>): Promise<T[]> {
  const out: T[] = [];
  for await (const item of gen) {
    out.push(item);
  }
  return out;
}

async function expectTransportError(
  promise: Promise<unknown>,
  kind: "network" | "aborted",
): Promise<TransportError> {
  let caught: unknown;
  try {
    await promise;
  } catch (error) {
    caught = error;
  }
  expect(caught).toBeInstanceOf(TransportError);
  const err = caught as TransportError;
  expect(err.kind).toBe(kind);
  return err;
}

/** 断言错误对象不携带 URL、请求头、API Key 与底层 cause。 */
function expectNoLeak(error: TransportError): void {
  const serialized = JSON.stringify({ ...error, message: error.message, name: error.name });
  expect(serialized).not.toContain(BASE_URL);
  expect(serialized).not.toContain(API_KEY);
  expect(serialized).not.toContain("Authorization");
  expect(serialized).not.toContain("X-Model-Api-Key");
  expect((error as { cause?: unknown }).cause).toBeUndefined();
}

// ---------- 构造 ----------

describe("WeChatTransport 构造", () => {
  it("baseUrl 必须是绝对 http(s) URL", () => {
    const reqHost = new FakeRequestHost();
    const streamHost = new FakeStreamHost();
    expect(
      () =>
        new WeChatTransport({
          baseUrl: "/v1",
          request: reqHost.adapter,
          streamRequest: streamHost.adapter,
        }),
    ).toThrowError(TransportError);
  });

  it("末尾斜杠会被去除并正确拼接路径", async () => {
    const reqHost = new FakeRequestHost();
    const streamHost = new FakeStreamHost();
    const transport = makeTransport(reqHost, streamHost);
    const pending = transport.request({ method: "GET", path: "/v1/health" });
    reqHost.last.success({ statusCode: 200, data: "ok" });
    await pending;
    expect(reqHost.last.url).toBe(`${BASE_URL}/v1/health`);
  });
});

// ---------- 普通请求 ----------

describe("WeChatTransport request", () => {
  it("透传 method/header/body 并返回 status/text", async () => {
    const reqHost = new FakeRequestHost();
    const transport = makeTransport(reqHost, new FakeStreamHost());
    const pending = transport.request({
      method: "POST",
      path: "/v1/sessions",
      headers: { "Content-Type": "application/json" },
      body: '{"provider":"openai"}',
    });
    const call = reqHost.last;
    expect(call.method).toBe("POST");
    expect(call.header).toEqual({ "Content-Type": "application/json" });
    expect(call.data).toBe('{"provider":"openai"}');
    call.success({ statusCode: 201, data: '{"data":{}}' });
    await expect(pending).resolves.toEqual({ status: 201, text: '{"data":{}}' });
  });

  it("宿主自动 JSON 解析时兜底还原为文本", async () => {
    const reqHost = new FakeRequestHost();
    const transport = makeTransport(reqHost, new FakeStreamHost());
    const pending = transport.request({ method: "GET", path: "/v1/x" });
    reqHost.last.success({ statusCode: 200, data: { data: { ok: true } } });
    const res = await pending;
    expect(JSON.parse(res.text)).toEqual({ data: { ok: true } });
  });

  it("fail 回调归一化为 network 且不泄漏细节", async () => {
    const reqHost = new FakeRequestHost();
    const transport = makeTransport(reqHost, new FakeStreamHost());
    const pending = transport.request({
      method: "POST",
      path: "/v1/turns",
      headers: { "X-Model-Api-Key": API_KEY },
    });
    reqHost.last.fail(new Error(`request to ${BASE_URL}/v1/turns failed, key=${API_KEY}`));
    const err = await expectTransportError(pending, "network");
    expectNoLeak(err);
  });

  it("适配器同步抛错归一化为 network", async () => {
    const transport = new WeChatTransport({
      baseUrl: BASE_URL,
      request: () => {
        throw new Error(`boom ${API_KEY}`);
      },
      streamRequest: new FakeStreamHost().adapter,
    });
    const err = await expectTransportError(
      transport.request({ method: "GET", path: "/v1/x" }),
      "network",
    );
    expectNoLeak(err);
  });

  it("预先取消：不调用宿主适配器直接抛 aborted", async () => {
    const reqHost = new FakeRequestHost();
    const transport = makeTransport(reqHost, new FakeStreamHost());
    const controller = new AbortController();
    controller.abort();
    await expectTransportError(
      transport.request({ method: "GET", path: "/v1/x", signal: controller.signal }),
      "aborted",
    );
    expect(reqHost.calls.length).toBe(0);
  });

  it("竞态：adapter 执行期间同步 abort（task 尚未返回）——返回后补 abort 任务且保持 aborted", async () => {
    const controller = new AbortController();
    let abortCount = 0;
    let capturedParams: WeChatRequestParams | undefined;
    const transport = new WeChatTransport({
      baseUrl: BASE_URL,
      request: (params) => {
        capturedParams = params;
        // 模拟：宿主在 adapter 内部（task 返回之前）触发了取消。
        controller.abort();
        return { abort: () => (abortCount += 1) };
      },
      streamRequest: new FakeStreamHost().adapter,
    });
    const err = await expectTransportError(
      transport.request({
        method: "POST",
        path: "/v1/turns",
        headers: { "X-Model-Api-Key": API_KEY },
        signal: controller.signal,
      }),
      "aborted",
    );
    expectNoLeak(err);
    // 关键断言：即使 onAbort 触发时 task 尚未赋值，返回后也必须补 abort 底层任务。
    expect(abortCount).toBe(1);
    // 宿主晚到的 success/fail 回调不改写 aborted 结果。
    expect(() => capturedParams?.success({ statusCode: 200, data: "late" })).not.toThrow();
    expect(() => capturedParams?.fail(new Error("request:fail abort"))).not.toThrow();
  });

  it("进行中取消：调用任务 abort 并抛 aborted；随后 fail 回调不改写结果", async () => {
    const reqHost = new FakeRequestHost();
    const transport = makeTransport(reqHost, new FakeStreamHost());
    const controller = new AbortController();
    const pending = transport.request({
      method: "GET",
      path: "/v1/x",
      signal: controller.signal,
    });
    controller.abort();
    const err = await expectTransportError(pending, "aborted");
    expectNoLeak(err);
    expect(reqHost.abortCount).toBe(1);
    // wx 在 abort 后仍会触发 fail：必须安全无副作用。
    expect(() => reqHost.last.fail(new Error("request:fail abort"))).not.toThrow();
  });
});

// ---------- 流式请求 ----------

describe("WeChatTransport stream", () => {
  it("SSE 字节流：状态、大小写不敏感 content-type、跨 chunk 解析", async () => {
    const streamHost = new FakeStreamHost();
    const transport = makeTransport(new FakeRequestHost(), streamHost);
    const pending = transport.stream({
      method: "POST",
      path: "/v1/turns",
      headers: { Authorization: "Bearer tok", "X-Model-Api-Key": API_KEY },
      body: '{"input":"你好"}',
    });
    const call = streamHost.last;
    expect(call.url).toBe(`${BASE_URL}/v1/turns`);
    expect(call.data).toBe('{"input":"你好"}');

    call.onHeaders({
      statusCode: 200,
      header: { "Content-Type": "text/event-stream; charset=utf-8" },
    });
    const stream = await pending;
    expect(stream.status).toBe(200);
    expect(stream.contentType).toBe("text/event-stream; charset=utf-8");

    // 一个事件跨两个 chunk + 第二个事件（含多字节 UTF-8）在同一 chunk。
    const frame1 = 'data: {"type":"turn_start"}\n\n';
    const frame2 = 'data: {"type":"text_delta","delta":"符文骑士"}\n\n';
    const bytes = encoder.encode(frame1 + frame2);
    call.onChunkReceived(bytes.slice(0, 9).buffer);
    call.onChunkReceived(bytes.slice(9));
    call.onComplete();

    const payloads = await collect(parseSseFrames(stream.body));
    expect(payloads).toEqual([
      '{"type":"turn_start"}',
      '{"type":"text_delta","delta":"符文骑士"}',
    ]);
  });

  it("ArrayBufferView 分块会被复制归一化，源缓冲复写不污染已产出数据", async () => {
    const streamHost = new FakeStreamHost();
    const transport = makeTransport(new FakeRequestHost(), streamHost);
    const pending = transport.stream({ method: "POST", path: "/v1/turns" });
    const call = streamHost.last;
    call.onHeaders({ statusCode: 200, header: { "content-type": "text/event-stream" } });
    const stream = await pending;

    const buffer = new Uint8Array([1, 2, 3, 4]);
    call.onChunkReceived(buffer.subarray(1, 3));
    buffer.fill(9);
    call.onComplete();
    const chunks = await collect(stream.body);
    expect(Array.from(chunks[0]!)).toEqual([2, 3]);
  });

  it("非 2xx 时 readText 汇总错误体（跨 chunk UTF-8）", async () => {
    const streamHost = new FakeStreamHost();
    const transport = makeTransport(new FakeRequestHost(), streamHost);
    const pending = transport.stream({ method: "POST", path: "/v1/turns" });
    const call = streamHost.last;
    call.onHeaders({ statusCode: 401, header: { "content-type": "application/json" } });
    const stream = await pending;
    expect(stream.status).toBe(401);

    const body = encoder.encode('{"error":{"code":"unauthorized","message":"会话无效"}}');
    // 故意在多字节字符中间切开。
    call.onChunkReceived(body.slice(0, 40));
    call.onChunkReceived(body.slice(40));
    call.onComplete();
    await expect(stream.readText()).resolves.toBe(
      '{"error":{"code":"unauthorized","message":"会话无效"}}',
    );
  });

  it("网络错误：先产出已到达字节，再抛 network 且不泄漏细节", async () => {
    const streamHost = new FakeStreamHost();
    const transport = makeTransport(new FakeRequestHost(), streamHost);
    const pending = transport.stream({
      method: "POST",
      path: "/v1/turns",
      headers: { "X-Model-Api-Key": API_KEY },
    });
    const call = streamHost.last;
    call.onHeaders({ statusCode: 200, header: { "content-type": "text/event-stream" } });
    const stream = await pending;

    call.onChunkReceived(encoder.encode("data: {}\n\n"));
    call.onError(new Error(`socket hang up at ${BASE_URL}, key=${API_KEY}`));

    const received: Uint8Array[] = [];
    let caught: unknown;
    try {
      for await (const chunk of stream.body) {
        received.push(chunk);
      }
    } catch (error) {
      caught = error;
    }
    expect(received.length).toBe(1);
    expect(caught).toBeInstanceOf(TransportError);
    const err = caught as TransportError;
    expect(err.kind).toBe("network");
    expectNoLeak(err);
  });

  it("响应头到达前失败：stream() 直接抛 network", async () => {
    const streamHost = new FakeStreamHost();
    const transport = makeTransport(new FakeRequestHost(), streamHost);
    const pending = transport.stream({ method: "POST", path: "/v1/turns" });
    streamHost.last.onError(new Error("ECONNREFUSED"));
    const err = await expectTransportError(pending, "network");
    expectNoLeak(err);
  });

  it("响应头到达前 onComplete：视为 network 异常", async () => {
    const streamHost = new FakeStreamHost();
    const transport = makeTransport(new FakeRequestHost(), streamHost);
    const pending = transport.stream({ method: "POST", path: "/v1/turns" });
    streamHost.last.onComplete();
    await expectTransportError(pending, "network");
  });

  it("预先取消：不调用宿主流式适配器直接抛 aborted", async () => {
    const streamHost = new FakeStreamHost();
    const transport = makeTransport(new FakeRequestHost(), streamHost);
    const controller = new AbortController();
    controller.abort();
    await expectTransportError(
      transport.stream({ method: "POST", path: "/v1/turns", signal: controller.signal }),
      "aborted",
    );
    expect(streamHost.calls.length).toBe(0);
  });

  it("竞态：流式 adapter 执行期间同步 abort（task 尚未返回）——返回后补 abort 任务且保持 aborted", async () => {
    const controller = new AbortController();
    let abortCount = 0;
    let capturedParams: WeChatStreamParams | undefined;
    const transport = new WeChatTransport({
      baseUrl: BASE_URL,
      request: new FakeRequestHost().adapter,
      streamRequest: (params) => {
        capturedParams = params;
        // 模拟：宿主在 adapter 内部（task 返回之前）触发了取消。
        controller.abort();
        return { abort: () => (abortCount += 1) };
      },
    });
    const err = await expectTransportError(
      transport.stream({
        method: "POST",
        path: "/v1/turns",
        headers: { "X-Model-Api-Key": API_KEY },
        signal: controller.signal,
      }),
      "aborted",
    );
    expectNoLeak(err);
    // 关键断言：onAbort 触发时 task 尚未赋值，返回后必须补 abort 底层任务。
    expect(abortCount).toBe(1);
    // 宿主晚到的回调（headers/chunk/error/complete）全部安全无副作用。
    expect(() =>
      capturedParams?.onHeaders({ statusCode: 200, header: { "content-type": "text/event-stream" } }),
    ).not.toThrow();
    expect(() => capturedParams?.onChunkReceived(new Uint8Array([1]))).not.toThrow();
    expect(() => capturedParams?.onError(new Error("request:fail abort"))).not.toThrow();
    expect(() => capturedParams?.onComplete()).not.toThrow();
  });

  it("等待响应头时取消：stream() 抛 aborted 并 abort 底层任务", async () => {
    const streamHost = new FakeStreamHost();
    const transport = makeTransport(new FakeRequestHost(), streamHost);
    const controller = new AbortController();
    const pending = transport.stream({
      method: "POST",
      path: "/v1/turns",
      signal: controller.signal,
    });
    controller.abort();
    await expectTransportError(pending, "aborted");
    expect(streamHost.abortCount).toBe(1);
  });

  it("迭代中取消：抛 aborted、abort 底层任务，且宿主后续回调安全", async () => {
    const streamHost = new FakeStreamHost();
    const transport = makeTransport(new FakeRequestHost(), streamHost);
    const controller = new AbortController();
    const pending = transport.stream({
      method: "POST",
      path: "/v1/turns",
      signal: controller.signal,
    });
    const call = streamHost.last;
    call.onHeaders({ statusCode: 200, header: { "content-type": "text/event-stream" } });
    const stream = await pending;
    call.onChunkReceived(encoder.encode("data: {}\n\n"));

    const iterator = stream.body[Symbol.asyncIterator]();
    const first = await iterator.next();
    expect(first.done).toBe(false);

    controller.abort();
    const err = await expectTransportError(iterator.next() as Promise<unknown>, "aborted");
    expectNoLeak(err);
    expect(streamHost.abortCount).toBe(1);
    // wx 在 abort 后仍可能触发 onError/onComplete：必须安全无副作用。
    expect(() => call.onError(new Error("request:fail abort"))).not.toThrow();
    expect(() => call.onComplete()).not.toThrow();
  });

  it("消费方提前 return：abort 底层任务停止下载", async () => {
    const streamHost = new FakeStreamHost();
    const transport = makeTransport(new FakeRequestHost(), streamHost);
    const pending = transport.stream({ method: "POST", path: "/v1/turns" });
    const call = streamHost.last;
    call.onHeaders({ statusCode: 200, header: { "content-type": "text/event-stream" } });
    const stream = await pending;
    call.onChunkReceived(encoder.encode("data: {}\n\n"));

    const iterator = stream.body[Symbol.asyncIterator]();
    await iterator.next();
    await iterator.return?.(undefined);
    expect(streamHost.abortCount).toBe(1);
  });
});

// ---------- 与 GatewayClient 的端到端集成 ----------

describe("GatewayClient over WeChatTransport", () => {
  it("createSession + runTurn 全链路：apiKey 只出现在请求头，事件正常产出", async () => {
    const reqHost = new FakeRequestHost();
    const streamHost = new FakeStreamHost();
    const client = new GatewayClient({ transport: makeTransport(reqHost, streamHost) });

    const created = client.createSession({
      provider: "openai",
      model: { id: "gpt-test" },
      baseUrl: "https://llm.example.com/v1",
    });
    reqHost.last.success({
      statusCode: 201,
      data: JSON.stringify({
        data: { sessionToken: "tok-1", expiresAt: "2030-01-01T00:00:00.000Z" },
      }),
    });
    const status = await created;
    expect(status.connected).toBe(true);
    expect(JSON.stringify(status)).not.toContain("tok-1");

    const events: GatewayEvent[] = [];
    const turn = (async () => {
      for await (const event of client.runTurn("你好", { apiKey: API_KEY })) {
        events.push(event);
      }
    })();

    // 等待流式请求发起。
    await new Promise((resolve) => setTimeout(resolve, 0));
    const call = streamHost.last;
    expect(call.header?.["X-Model-Api-Key"]).toBe(API_KEY);
    expect(call.header?.Authorization).toBe("Bearer tok-1");
    // apiKey 不进入 URL 与请求体。
    expect(call.url).not.toContain(API_KEY);
    expect(call.data ?? "").not.toContain(API_KEY);

    call.onHeaders({ statusCode: 200, header: { "Content-Type": "text/event-stream" } });
    call.onChunkReceived(
      encoder.encode(
        'data: {"type":"turn_start"}\n\n' +
          'data: {"type":"text_delta","delta":"hi"}\n\n' +
          'data: {"type":"done"}\n\n',
      ),
    );
    call.onComplete();
    await turn;
    expect(events.map((e) => e.type)).toEqual(["turn_start", "text_delta", "done"]);
  });

  it("transport 实例不残留 apiKey（枚举自有属性检查）", async () => {
    const reqHost = new FakeRequestHost();
    const streamHost = new FakeStreamHost();
    const transport = makeTransport(reqHost, streamHost);
    const pending = transport.stream({
      method: "POST",
      path: "/v1/turns",
      headers: { "X-Model-Api-Key": API_KEY },
    });
    streamHost.last.onHeaders({ statusCode: 200, header: { "content-type": "text/event-stream" } });
    streamHost.last.onComplete();
    await pending;
    // #private 字段不可枚举；确保没有把凭据写到可枚举属性上。
    expect(JSON.stringify(Object.entries(transport))).not.toContain(API_KEY);
  });
});
