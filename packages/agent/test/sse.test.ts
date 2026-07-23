import { describe, expect, it } from "vitest";
import { parseSseStream } from "../src/providers/sse.ts";

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<string[]> {
  const payloads: string[] = [];
  for await (const payload of parseSseStream(stream)) {
    payloads.push(payload);
  }
  return payloads;
}

describe("parseSseStream", () => {
  it("解析单个事件", async () => {
    expect(await collect(streamOf('data: {"a":1}\n\n'))).toEqual(['{"a":1}']);
  });

  it("行可以在任意 chunk 边界断开", async () => {
    const payloads = await collect(streamOf(
      "da",
      'ta: {"hel',
      'lo":"wor',
      'ld"}',
      "\n",
      "\n",
    ));
    expect(payloads).toEqual(['{"hello":"world"}']);
  });

  it("一个 chunk 可以包含多个事件与多行", async () => {
    const payloads = await collect(streamOf(
      'data: {"a":1}\n\ndata: {"b":2}\n\ndata: [DONE]\n\n',
    ));
    expect(payloads).toEqual(['{"a":1}', '{"b":2}', "[DONE]"]);
  });

  it("同一事件的多个 data 行按规范用换行连接", async () => {
    const payloads = await collect(streamOf("data: line1\ndata: line2\n\n"));
    expect(payloads).toEqual(["line1\nline2"]);
  });

  it("支持 CRLF 行结束符", async () => {
    const payloads = await collect(streamOf('data: {"a":1}\r\n\r\ndata: [DONE]\r\n\r\n'));
    expect(payloads).toEqual(['{"a":1}', "[DONE]"]);
  });

  it("忽略注释与非 data 字段", async () => {
    const payloads = await collect(streamOf(
      ": keep-alive\nevent: message\nid: 42\ndata: payload\n\n",
    ));
    expect(payloads).toEqual(["payload"]);
  });

  it("流结束时冲刷缺少结尾空行的事件", async () => {
    const payloads = await collect(streamOf("data: tail-event"));
    expect(payloads).toEqual(["tail-event"]);
  });

  it("多字节 UTF-8 字符跨 chunk 断开时仍能正确解码", async () => {
    const encoder = new TextEncoder();
    const bytes = encoder.encode("data: 借机攻击\n\n");
    const middle = 9; // 切在中文字符的多字节序列内部
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, middle));
        controller.enqueue(bytes.slice(middle));
        controller.close();
      },
    });
    expect(await collect(stream)).toEqual(["借机攻击"]);
  });
});
