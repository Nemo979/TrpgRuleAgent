import { describe, expect, it } from "vitest";
import { TransportError, WeChatTransport } from "@trpg-rule-agent/gateway-client";
import { createWeChatAdapters } from "../src/wx-host.ts";
import type { WxHost, WxHostRequestOptions, WxHostRequestTask } from "../src/wx-host.ts";

/** 可编程 fake 任务：测试侧手动驱动回调（模拟 wx 的异步时序）。 */
class FakeTask implements WxHostRequestTask {
  aborted = false;
  headerListener: ((res: { statusCode?: number; header?: Record<string, string> }) => void) | undefined;
  chunkListener: ((res: { data: ArrayBuffer | ArrayBufferView }) => void) | undefined;

  abort(): void {
    this.aborted = true;
  }

  onHeadersReceived(listener: (res: { statusCode?: number; header?: Record<string, string> }) => void): void {
    this.headerListener = listener;
  }

  onChunkReceived(listener: (res: { data: ArrayBuffer | ArrayBufferView }) => void): void {
    this.chunkListener = listener;
  }
}

interface Captured {
  options: WxHostRequestOptions;
  task: FakeTask;
}

/** 构造 fake 宿主：捕获每次 request 的入参与任务句柄。 */
function fakeHost(): { host: WxHost; calls: Captured[] } {
  const calls: Captured[] = [];
  const host: WxHost = {
    request(options) {
      const task = new FakeTask();
      calls.push({ options, task });
      return task;
    },
  };
  return { host, calls };
}

function bytes(text: string): ArrayBuffer {
  const view = new TextEncoder().encode(text);
  return view.buffer.slice(view.byteOffset, view.byteOffset + view.byteLength) as ArrayBuffer;
}

describe("createWeChatAdapters / request 适配器", () => {
  it("关闭自动 JSON 解析并透传 url/method/header/data", () => {
    const { host, calls } = fakeHost();
    const { request } = createWeChatAdapters(host);

    request({
      url: "https://gw.example.com/v1/sessions",
      method: "POST",
      header: { "Content-Type": "application/json" },
      data: '{"a":1}',
      success: () => {},
      fail: () => {},
    });

    expect(calls).toHaveLength(1);
    const options = calls[0]!.options;
    expect(options.url).toBe("https://gw.example.com/v1/sessions");
    expect(options.method).toBe("POST");
    expect(options.dataType).toBe("其他");
    expect(options.responseType).toBe("text");
    expect(options.header).toEqual({ "Content-Type": "application/json" });
    expect(options.data).toBe('{"a":1}');
    expect(options.enableChunked).toBeUndefined();
  });

  it("success 回调映射 statusCode/data；缺 statusCode 时兜底为 0", () => {
    const { host, calls } = fakeHost();
    const { request } = createWeChatAdapters(host);

    const received: Array<{ statusCode: number; data: unknown }> = [];
    request({
      url: "https://gw.example.com/v1/sessions",
      method: "POST",
      success: (res) => received.push(res),
      fail: () => {},
    });

    calls[0]!.options.success?.({ statusCode: 201, data: '{"ok":true}' });
    calls[0]!.options.success?.({ data: "no-status" });
    expect(received).toEqual([
      { statusCode: 201, data: '{"ok":true}' },
      { statusCode: 0, data: "no-status" },
    ]);
  });

  it("返回的任务句柄 abort 会取消底层 wx 任务", () => {
    const { host, calls } = fakeHost();
    const { request } = createWeChatAdapters(host);

    const task = request({
      url: "https://gw.example.com/v1/session",
      method: "DELETE",
      success: () => {},
      fail: () => {},
    });
    task?.abort?.();
    expect(calls[0]!.task.aborted).toBe(true);
  });

  it("不把宿主 wx 错误对象透传给 SDK（fail 只收到固定文案占位错误）", () => {
    const { host, calls } = fakeHost();
    const { request } = createWeChatAdapters(host);

    const hostError = new Error(
      "request:fail https://gw.example.com/v1/sessions?leak=1 errMsg detail",
    );
    let sdkReceived: unknown;
    request({
      url: "https://gw.example.com/v1/sessions",
      method: "POST",
      success: () => {},
      fail: (err) => {
        sdkReceived = err;
      },
    });
    calls[0]!.options.fail?.(hostError);

    expect(sdkReceived).not.toBe(hostError);
    const text = String((sdkReceived as Error).message);
    expect(text).not.toContain("gw.example.com");
    expect(text).not.toContain("errMsg");
  });
});

describe("createWeChatAdapters / streamRequest 适配器", () => {
  function makeStream() {
    const { host, calls } = fakeHost();
    const { streamRequest } = createWeChatAdapters(host);
    const events: string[] = [];
    const headers: Array<{ statusCode: number; header?: Record<string, string> }> = [];
    const chunks: Array<ArrayBuffer | ArrayBufferView> = [];
    const errors: unknown[] = [];
    const task = streamRequest({
      url: "https://gw.example.com/v1/turns",
      method: "POST",
      header: { Authorization: "Bearer tok" },
      data: '{"input":"hi"}',
      onHeaders: (info) => {
        events.push("headers");
        headers.push(info);
      },
      onChunkReceived: (chunk) => {
        events.push("chunk");
        chunks.push(chunk);
      },
      onComplete: () => events.push("complete"),
      onError: (err) => {
        events.push("error");
        errors.push(err);
      },
    });
    return { calls, events, headers, chunks, errors, task };
  }

  it("开启 enableChunked 并透传 header/data", () => {
    const { calls } = makeStream();
    const options = calls[0]!.options;
    expect(options.enableChunked).toBe(true);
    expect(options.header).toEqual({ Authorization: "Bearer tok" });
    expect(options.data).toBe('{"input":"hi"}');
  });

  it("onHeadersReceived 缺 statusCode 时兜底为 200，且先于 chunk 派发", () => {
    const { calls, events, headers } = makeStream();
    const wxTask = calls[0]!.task;
    wxTask.headerListener?.({ header: { "Content-Type": "text/event-stream" } });
    wxTask.chunkListener?.({ data: bytes("data: {}\n\n") });
    calls[0]!.options.success?.({ statusCode: 200 });

    expect(events).toEqual(["headers", "chunk", "complete"]);
    expect(headers[0]).toEqual({
      statusCode: 200,
      header: { "Content-Type": "text/event-stream" },
    });
  });

  it("chunk 先于响应头到达时缓存 chunk（不伪造响应头），待真实响应头到达后按序回放", () => {
    const { calls, events, headers, chunks } = makeStream();
    const wxTask = calls[0]!.task;

    // 早到的两个 chunk：此时不应派发任何 headers/chunk（只缓存）。
    wxTask.chunkListener?.({ data: bytes("chunk-1") });
    wxTask.chunkListener?.({ data: bytes("chunk-2") });
    expect(events).toEqual([]);

    // 真实响应头到达（真实状态码/内容类型）：先 onHeaders，再按原序回放缓存 chunk。
    wxTask.headerListener?.({ statusCode: 206, header: { "content-type": "text/event-stream" } });
    expect(events).toEqual(["headers", "chunk", "chunk"]);
    expect(headers[0]).toEqual({
      statusCode: 206,
      header: { "content-type": "text/event-stream" },
    });
    const decoder = new TextDecoder();
    expect(chunks.map((c) => decoder.decode(c as ArrayBuffer))).toEqual(["chunk-1", "chunk-2"]);

    // 响应头之后到达的 chunk 立即透传。
    wxTask.chunkListener?.({ data: bytes("chunk-3") });
    expect(events).toEqual(["headers", "chunk", "chunk", "chunk"]);
  });

  it("绝不伪造 200/text-event-stream：非 SSE 的真实响应头被如实透传给 SDK", () => {
    const { calls, events, headers } = makeStream();
    const wxTask = calls[0]!.task;

    // 早到 chunk（例如错误响应体）先被缓存，不得触发 200 event-stream 伪造。
    wxTask.chunkListener?.({ data: bytes("{\"error\":\"boom\"}") });
    expect(events).toEqual([]);

    // 真实响应头是 500 + application/json：必须原样上抛，让 SDK 走错误分支。
    wxTask.headerListener?.({ statusCode: 500, header: { "content-type": "application/json" } });
    expect(headers[0]).toEqual({
      statusCode: 500,
      header: { "content-type": "application/json" },
    });
  });

  it("空流（无 chunk 即 success）时用 success 的 statusCode 兜底补发响应头", () => {
    const { calls, events, headers } = makeStream();
    calls[0]!.options.success?.({ statusCode: 401, header: { "Content-Type": "application/json" } });

    expect(events).toEqual(["headers", "complete"]);
    expect(headers[0]).toEqual({
      statusCode: 401,
      header: { "Content-Type": "application/json" },
    });
  });

  it("不把宿主 wx 错误对象透传给 SDK（onError 只收到固定文案占位错误）", () => {
    const { calls, errors } = makeStream();
    const hostError = new Error("request:fail abort https://gw.example.com/v1/turns errMsg");
    calls[0]!.options.fail?.(hostError);

    expect(errors).toHaveLength(1);
    expect(errors[0]).not.toBe(hostError);
    const text = String((errors[0] as Error).message);
    expect(text).not.toContain("gw.example.com");
    expect(text).not.toContain("errMsg");
  });

  it("abort 透传到底层 wx 任务", () => {
    const { calls, task } = makeStream();
    task?.abort?.();
    expect(calls[0]!.task.aborted).toBe(true);
  });
});

describe("与 WeChatTransport 的端到端契约", () => {
  it("流式响应经适配层交给 WeChatTransport 后可按字节块异步迭代", async () => {
    const { host, calls } = fakeHost();
    const adapters = createWeChatAdapters(host);
    const transport = new WeChatTransport({
      baseUrl: "https://gw.example.com",
      request: adapters.request,
      streamRequest: adapters.streamRequest,
    });

    const streamPromise = transport.stream({
      method: "POST",
      path: "/v1/turns",
      headers: { Authorization: "Bearer tok" },
      body: "{}",
    });
    const wx = calls[0]!;
    wx.task.headerListener?.({ header: { "content-type": "text/event-stream" } });
    wx.task.chunkListener?.({ data: bytes("data: {\"type\":\"done\"}\n\n") });
    wx.options.success?.({ statusCode: 200 });

    const stream = await streamPromise;
    expect(stream.status).toBe(200);
    expect(stream.contentType).toBe("text/event-stream");
    const decoder = new TextDecoder();
    let text = "";
    for await (const chunk of stream.body) {
      text += decoder.decode(chunk, { stream: true });
    }
    expect(text).toBe('data: {"type":"done"}\n\n');
  });

  it("宿主传输失败被归一化为固定文案的 TransportError，不泄漏 wx 错误细节", async () => {
    const { host, calls } = fakeHost();
    const adapters = createWeChatAdapters(host);
    const transport = new WeChatTransport({
      baseUrl: "https://gw.example.com",
      request: adapters.request,
      streamRequest: adapters.streamRequest,
    });

    const streamPromise = transport.stream({ method: "POST", path: "/v1/turns", body: "{}" });
    calls[0]!.options.fail?.(
      new Error("request:fail https://gw.example.com/v1/turns?apiKey=sk-leak errMsg"),
    );

    const error = await streamPromise.then(
      () => undefined,
      (err: unknown) => err,
    );
    expect(error).toBeInstanceOf(TransportError);
    expect((error as TransportError).kind).toBe("network");
    expect((error as TransportError).message).toBe("网络请求失败");
    expect(String(error)).not.toContain("sk-leak");
    expect(String(error)).not.toContain("gw.example.com");
  });
});
