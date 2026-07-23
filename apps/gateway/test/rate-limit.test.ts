import { describe, expect, it } from "vitest";
import { RequestRateLimiter } from "../src/rate-limit.ts";

describe("RequestRateLimiter", () => {
  it("限制窗口内的来源请求数，并在窗口结束后重置", () => {
    const limiter = new RequestRateLimiter(1_000, 2);

    expect(limiter.allow("127.0.0.1", 10_000)).toBe(true);
    expect(limiter.allow("127.0.0.1", 10_100)).toBe(true);
    expect(limiter.allow("127.0.0.1", 10_200)).toBe(false);
    expect(limiter.allow("127.0.0.2", 10_200)).toBe(true);
    expect(limiter.allow("127.0.0.1", 11_000)).toBe(true);
  });

  it("不把不同来源合并到同一个桶", () => {
    const limiter = new RequestRateLimiter(60_000, 1);
    expect(limiter.allow("a", 1)).toBe(true);
    expect(limiter.allow("b", 1)).toBe(true);
    expect(limiter.allow("a", 2)).toBe(false);
  });
});
