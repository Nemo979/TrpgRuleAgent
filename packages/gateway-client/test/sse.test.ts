import { describe, expect, it } from "vitest";
import { TextEncoder } from "node:util";
import { decodeGatewayEvent, parseSseFrames } from "../src/sse.ts";
import { GatewayClientError } from "../src/errors.ts";

// ---------- 测试基础设施 ----------

const encoder = new TextEncoder();

/** 把若干字符串 chunk 编码成 Uint8Array 并做成 async iterable。 */
async function* chunksOf(...chunks: string[]): AsyncIterable<Uint8Array> {
  for (const chunk of chunks) {
    yield encoder.encode(chunk);
  }
}

/** 按 1 字节切分整段文本为多个 Uint8Array。 */
async function* byteByByte(text: string): AsyncIterable<Uint8Array> {
  const bytes = encoder.encode(text);
  for (const byte of bytes) {
    yield new Uint8Array([byte]);
  }
}

/** 直接把若干原始字节块作为 Uint8Array 喂入（用于跨字节切分测试）。 */
async function* chunksOfBytes(...chunks: Uint8Array[]): AsyncIterable<Uint8Array> {
  for (const chunk of chunks) {
    yield chunk;
  }
}

/** 收集 parseSseFrames 产出的所有 data 载荷。 */
async function collectFrames(source: AsyncIterable<Uint8Array>): Promise<string[]> {
  const frames: string[] = [];
  for await (const frame of parseSseFrames(source)) {
    frames.push(frame);
  }
  return frames;
}

/** 断言 fn 抛出指定 code 的 GatewayClientError，返回该 error。 */
function expectGatewayError(code: string, fn: () => unknown): GatewayClientError {
  let thrown: unknown;
  try {
    fn();
  } catch (e) {
    thrown = e;
  }
  expect(thrown).toBeInstanceOf(GatewayClientError);
  const err = thrown as GatewayClientError;
  expect(err.code).toBe(code);
  return err;
}

// ---------- parseSseFrames ----------

describe("parseSseFrames", () => {
  it("解析单条事件", async () => {
    const frames = await collectFrames(chunksOf('data: {"a":1}\n\n'));
    expect(frames).toEqual(['{"a":1}']);
  });

  it("逐字节碎片切分仍能解析一个事件", async () => {
    const message = 'data: {"type":"text_delta","delta":"hello"}\n\n';
    const frames = await collectFrames(byteByByte(message));
    expect(frames).toEqual(['{"type":"text_delta","delta":"hello"}']);
  });

  it("UTF-8 跨块：整段原始字节在【任意】位置切成两块（含多字节字符中间）均正确解码", async () => {
    // 直接对原始 bytes 分块：绝不先解码再重编码（那样无法制造半个码点的块）。
    const full = 'data: {"delta":"攻击"}\n\n';
    const bytes = encoder.encode(full);
    for (let cut = 1; cut < bytes.length; cut++) {
      const frames = await collectFrames(
        chunksOfBytes(bytes.subarray(0, cut), bytes.subarray(cut)),
      );
      expect(frames).toEqual(['{"delta":"攻击"}']);
    }
  });

  it("UTF-8 跨块：4 字节 emoji 被逐字节切分仍正确解码", async () => {
    const full = 'data: {"delta":"🎲骰"}\n\n';
    const bytes = encoder.encode(full);
    const oneByOne = Array.from(bytes, (b) => new Uint8Array([b]));
    const frames = await collectFrames(chunksOfBytes(...oneByOne));
    expect(frames).toEqual(['{"delta":"🎲骰"}']);
  });

  it("CRLF 行结束符也能解析", async () => {
    const frames = await collectFrames(chunksOf('data: {"a":1}\r\n\r\ndata: [DONE]\r\n\r\n'));
    expect(frames).toEqual(['{"a":1}', "[DONE]"]);
  });

  it("一个 chunk 含多个事件依次产出", async () => {
    const frames = await collectFrames(chunksOf(
      'data: {"a":1}\n\ndata: {"b":2}\n\ndata: [DONE]\n\n',
    ));
    expect(frames).toEqual(['{"a":1}', '{"b":2}', "[DONE]"]);
  });

  it("同一事件多行 data: 按 \\n 连接", async () => {
    const frames = await collectFrames(chunksOf("data: line1\ndata: line2\n\n"));
    expect(frames).toEqual(["line1\nline2"]);
  });

  it("尾部事件：结尾缺空行时残留 data 被冲刷", async () => {
    const frames = await collectFrames(chunksOf("data: tail-event"));
    expect(frames).toEqual(["tail-event"]);
  });

  it("结尾已是一整行但缺空行也会被冲刷", async () => {
    const frames = await collectFrames(chunksOf('data: {"a":1}\n'));
    expect(frames).toEqual(['{"a":1}']);
  });

  it("忽略注释行与 event:/id: 字段", async () => {
    const frames = await collectFrames(chunksOf(
      ": keep-alive\nevent: message\nid: 42\ndata: payload\n\n",
    ));
    expect(frames).toEqual(["payload"]);
  });
});

// ---------- decodeGatewayEvent ----------

describe("decodeGatewayEvent", () => {
  it("解析各合法事件类型", () => {
    expect(decodeGatewayEvent('{"type":"turn_start"}')).toEqual({ type: "turn_start" });
    expect(decodeGatewayEvent('{"type":"text_delta","delta":"hi"}')).toEqual({
      type: "text_delta",
      delta: "hi",
    });
    expect(
      decodeGatewayEvent('{"type":"tool_start","toolCallId":"t1","toolName":"search"}'),
    ).toEqual({ type: "tool_start", toolCallId: "t1", toolName: "search" });
    expect(
      decodeGatewayEvent('{"type":"tool_end","toolCallId":"t1","toolName":"search"}'),
    ).toEqual({ type: "tool_end", toolCallId: "t1", toolName: "search" });
    expect(
      decodeGatewayEvent(
        '{"type":"tool_error","toolCallId":"t1","toolName":"search","error":{"category":"c","message":"m"}}',
      ),
    ).toEqual({
      type: "tool_error",
      toolCallId: "t1",
      toolName: "search",
      error: { category: "c", message: "m" },
    });
    expect(decodeGatewayEvent('{"type":"turn_end"}')).toEqual({ type: "turn_end" });
    expect(decodeGatewayEvent('{"type":"error","error":{"category":"c","message":"m"}}')).toEqual({
      type: "error",
      error: { category: "c", message: "m" },
    });
    expect(
      decodeGatewayEvent(
        '{"type":"sources","sources":[{"label":"L","documentId":"d","fullPath":"p","title":"T"}]}',
      ),
    ).toEqual({
      type: "sources",
      sources: [{ label: "L", documentId: "d", fullPath: "p", title: "T" }],
    });
    expect(decodeGatewayEvent('{"type":"done"}')).toEqual({ type: "done" });
  });

  it("sources 缺少可选 title 时仍合法", () => {
    const event = decodeGatewayEvent(
      '{"type":"sources","sources":[{"label":"L","documentId":"d","fullPath":"p"}]}',
    );
    expect(event).toEqual({
      type: "sources",
      sources: [{ label: "L", documentId: "d", fullPath: "p" }],
    });
  });

  it("畸形 JSON -> malformed_event，且 message 不含原始 payload", () => {
    const payload = "not-json{";
    const err = expectGatewayError("malformed_event", () => decodeGatewayEvent(payload));
    expect(err.message).not.toContain(payload);
  });

  it("未知事件类型 -> protocol_error", () => {
    expectGatewayError("protocol_error", () => decodeGatewayEvent('{"type":"frobnicate"}'));
  });

  it("非对象 JSON -> protocol_error", () => {
    expectGatewayError("protocol_error", () => decodeGatewayEvent("[1,2,3]"));
  });

  it("text_delta 缺 delta -> protocol_error", () => {
    expectGatewayError("protocol_error", () => decodeGatewayEvent('{"type":"text_delta"}'));
  });

  it("sources 不是数组 -> protocol_error", () => {
    expectGatewayError("protocol_error", () => decodeGatewayEvent('{"type":"sources","sources":{}}'));
  });

  it("sources 元素缺字段 -> protocol_error", () => {
    expectGatewayError(
      "protocol_error",
      () => decodeGatewayEvent('{"type":"sources","sources":[{"label":"L"}]}'),
    );
  });

  it("传入 secret 时 error 事件 message 中的 secret 被替换为 [REDACTED]", () => {
    const secret = "sk-super-secret-123";
    const event = decodeGatewayEvent(
      `{"type":"error","error":{"category":"c","message":"oops ${secret} leaked"}}`,
      secret,
    );
    expect(event).toEqual({
      type: "error",
      error: { category: "c", message: "oops [REDACTED] leaked" },
    });
  });

  it("未传入 secret 时 error message 原样保留", () => {
    const event = decodeGatewayEvent(
      '{"type":"error","error":{"category":"c","message":"oops secret leaked"}}',
    );
    expect(event).toEqual({
      type: "error",
      error: { category: "c", message: "oops secret leaked" },
    });
  });
});
