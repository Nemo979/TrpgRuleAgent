import { describe, expect, it } from "vitest";
import { TransportError } from "@trpg-rule-agent/gateway-client";
import { MockTransport } from "../src/mock-transport.ts";

const decoder = new TextDecoder();

/** 迭代 mock 的 SSE 字节流，返回收集到的帧文本；若抛错则一并返回。不涉及任何真实网络。 */
async function drain(
  body: AsyncIterable<Uint8Array>,
  onFrame?: (index: number) => void,
): Promise<{ frames: string[]; error: unknown }> {
  const frames: string[] = [];
  let error: unknown = null;
  try {
    let idx = 0;
    for await (const chunk of body) {
      frames.push(decoder.decode(chunk));
      onFrame?.(idx);
      idx += 1;
    }
  } catch (e) {
    error = e;
  }
  return { frames, error };
}

describe("MockTransport（无真实网络）", () => {
  it("不取消时完整产出脚本化事件流，以 done 结尾", async () => {
    const transport = new MockTransport();
    const res = await transport.stream({ method: "POST", path: "/v1/turns" });
    expect(res.status).toBe(200);
    expect(res.contentType).toBe("text/event-stream");
    const { frames, error } = await drain(res.body);
    expect(error).toBeNull();
    expect(frames.length).toBeGreaterThan(0);
    expect(frames[frames.length - 1]).toContain('"done"');
  });

  it("中途 abort：立即抛 TransportError(aborted) 且不再继续产出后续帧", async () => {
    const transport = new MockTransport();
    const controller = new AbortController();
    const res = await transport.stream({
      method: "POST",
      path: "/v1/turns",
      signal: controller.signal,
    });

    // 收到第 2 帧（index 1）后取消，验证不会跑完整段脚本。
    const { frames, error } = await drain(res.body, (index) => {
      if (index === 1) {
        controller.abort();
      }
    });

    expect(error).toBeInstanceOf(TransportError);
    expect((error as TransportError).kind).toBe("aborted");
    // 完整脚本共 9 帧（turn_start + 3×text_delta + tool_start/end + turn_end + sources + done）；
    // 收到第 2 帧后取消，产出数应远小于 9，且不含结尾 done。
    expect(frames.length).toBeLessThan(9);
    expect(frames.some((f) => f.includes('"done"'))).toBe(false);
  });

  it("信号在开始前已取消：迭代立即抛 aborted，零产出", async () => {
    const transport = new MockTransport();
    const controller = new AbortController();
    controller.abort();
    const res = await transport.stream({
      method: "POST",
      path: "/v1/turns",
      signal: controller.signal,
    });
    const { frames, error } = await drain(res.body);
    expect(frames).toHaveLength(0);
    expect(error).toBeInstanceOf(TransportError);
    expect((error as TransportError).kind).toBe("aborted");
  });

  it("request：信号已取消时抛 aborted（不返回会话 token）", async () => {
    const transport = new MockTransport();
    const controller = new AbortController();
    controller.abort();
    await expect(
      transport.request({ method: "POST", path: "/v1/sessions", signal: controller.signal }),
    ).rejects.toBeInstanceOf(TransportError);
  });
});
