import { describe, expect, it } from "vitest";
import {
  AllowlistModelEndpointPolicy,
  type ModelEndpointPolicy,
} from "../src/endpoint-policy.ts";
import { GatewayError } from "../src/errors.ts";

function status(fn: () => void): number | undefined {
  try {
    fn();
    return undefined;
  } catch (error) {
    if (error instanceof GatewayError) {
      return error.status;
    }
    throw error;
  }
}

describe("AllowlistModelEndpointPolicy", () => {
  const policy: ModelEndpointPolicy = new AllowlistModelEndpointPolicy({
    allowlist: ["https://api.openai.com/v1", "https://llm.example"],
  });

  it("前缀命中（含子路径）放行", () => {
    expect(status(() => policy.assertAllowed("https://api.openai.com/v1"))).toBeUndefined();
    expect(status(() => policy.assertAllowed("https://api.openai.com/v1/"))).toBeUndefined();
    expect(status(() => policy.assertAllowed("https://api.openai.com/v1/chat"))).toBeUndefined();
    // origin-only 条目放行任意路径。
    expect(status(() => policy.assertAllowed("https://llm.example/anything/v1"))).toBeUndefined();
  });

  it("路径越界拒绝：兄弟路径、前缀假匹配（403）", () => {
    // 同 origin 但不在 /v1 边界内。
    expect(status(() => policy.assertAllowed("https://api.openai.com/admin"))).toBe(403);
    expect(status(() => policy.assertAllowed("https://api.openai.com/"))).toBe(403);
    // 字符串前缀相同但路径段不同：/v1x 不是 /v1 的子路径。
    expect(status(() => policy.assertAllowed("https://api.openai.com/v1x"))).toBe(403);
    expect(status(() => policy.assertAllowed("https://api.openai.com/v10/chat"))).toBe(403);
  });

  it("不同 origin 拒绝（403）：子域、端口、协议差异", () => {
    expect(status(() => policy.assertAllowed("https://evil.example/v1"))).toBe(403);
    expect(status(() => policy.assertAllowed("https://sub.llm.example/v1"))).toBe(403);
    expect(status(() => policy.assertAllowed("https://llm.example:8443/v1"))).toBe(403);
  });

  it("非本机端点必须 HTTPS（400）", () => {
    expect(status(() => policy.assertAllowed("http://llm.example/v1"))).toBe(400);
  });

  it("拒绝 userinfo / query / fragment / 非法 URL（400）", () => {
    expect(status(() => policy.assertAllowed("https://user:pass@llm.example/v1"))).toBe(400);
    expect(status(() => policy.assertAllowed("https://llm.example/v1?x=1"))).toBe(400);
    expect(status(() => policy.assertAllowed("https://llm.example/v1#frag"))).toBe(400);
    expect(status(() => policy.assertAllowed("not-a-url"))).toBe(400);
  });

  it("本机端点默认拒绝（403）", () => {
    expect(status(() => policy.assertAllowed("http://127.0.0.1:1234/v1"))).toBe(403);
    expect(status(() => policy.assertAllowed("http://localhost:1234/v1"))).toBe(403);
  });

  it("开启 allowLocalhost 后放行本机 HTTP 端点，且无需白名单", () => {
    const dev = new AllowlistModelEndpointPolicy({ allowlist: [], allowLocalhost: true });
    expect(status(() => dev.assertAllowed("http://127.0.0.1:1234/v1"))).toBeUndefined();
    expect(status(() => dev.assertAllowed("http://localhost:8080/v1"))).toBeUndefined();
    // 本机端点同样禁止 query/userinfo。
    expect(status(() => dev.assertAllowed("http://127.0.0.1:1234/v1?k=1"))).toBe(400);
    // 非本机仍然走 HTTPS + 白名单。
    expect(status(() => dev.assertAllowed("https://llm.example/v1"))).toBe(403);
  });

  it("白名单条目本身在构造时校验：非 HTTPS、userinfo、query、fragment、非法 URL", () => {
    for (const entry of [
      "http://llm.example/v1",
      "https://u:p@llm.example/v1",
      "https://llm.example/v1?x=1",
      "https://llm.example/v1#frag",
      "not-a-url",
    ]) {
      expect(() => new AllowlistModelEndpointPolicy({ allowlist: [entry] })).toThrowError(
        GatewayError,
      );
    }
  });

  it("本机白名单条目仅在 allowLocalhost 开启时可用", () => {
    expect(
      () => new AllowlistModelEndpointPolicy({ allowlist: ["http://127.0.0.1:8080/v1"] }),
    ).toThrowError(GatewayError);
    expect(
      () =>
        new AllowlistModelEndpointPolicy({
          allowlist: ["http://127.0.0.1:8080/v1"],
          allowLocalhost: true,
        }),
    ).not.toThrow();
  });

  it("条目尾部斜杠归一化：'/v1/' 与 '/v1' 等价", () => {
    const slash = new AllowlistModelEndpointPolicy({ allowlist: ["https://llm.example/v1/"] });
    expect(status(() => slash.assertAllowed("https://llm.example/v1"))).toBeUndefined();
    expect(status(() => slash.assertAllowed("https://llm.example/v1/chat"))).toBeUndefined();
    expect(status(() => slash.assertAllowed("https://llm.example/v1x"))).toBe(403);
  });
});
