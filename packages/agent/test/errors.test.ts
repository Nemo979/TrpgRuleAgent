import { describe, expect, it } from "vitest";
import { AgentError, REDACTED, redactAgentError, redactSecret } from "../src/core/index.ts";

describe("redactSecret", () => {
  it("替换文本中出现的所有密钥", () => {
    const secret = "sk-abcdef123456";
    const text = `first ${secret} middle ${secret} end`;
    expect(redactSecret(text, secret)).toBe(`first ${REDACTED} middle ${REDACTED} end`);
  });

  it("密钥为空（undefined 或空串）时才跳过", () => {
    expect(redactSecret("hello", undefined)).toBe("hello");
    expect(redactSecret("hello", "")).toBe("hello");
  });

  it("任何非空短密钥（长度 1、2、3）都必须被替换", () => {
    expect(redactSecret("token a here", "a")).toBe(`token ${REDACTED} here`);
    expect(redactSecret("key=ab end", "ab")).toBe(`key=${REDACTED} end`);
    expect(redactSecret("x=abc; y=abc", "abc")).toBe(`x=${REDACTED}; y=${REDACTED}`);
  });
});

describe("redactAgentError", () => {
  it("脱敏顶层 message 并保留 category、status", () => {
    const secret = "sk-supersecret-1234567890";
    const cause = new Error("underlying");
    const original = new AgentError("provider_http_error", `HTTP 401 key ${secret}`, {
      cause,
      status: 401,
    });

    const redacted = redactAgentError(original, secret);

    expect(redacted.message).toBe(`HTTP 401 key ${REDACTED}`);
    expect(redacted.category).toBe("provider_http_error");
    expect(redacted.status).toBe(401);
  });

  it("顶层 message 不含密钥但 cause.message 含密钥时，仍安全化 cause", () => {
    const secret = "sk-supersecret-1234567890";
    const cause = new Error(`connect failed using key ${secret}`);
    cause.name = "TypeError";
    const original = new AgentError("provider_http_error", "无法连接模型服务", {
      cause,
      status: 502,
    });

    const redacted = redactAgentError(original, secret);

    // 不能直接返回原错误
    expect(redacted).not.toBe(original);
    expect(redacted.message).not.toContain(secret);
    expect(redacted.category).toBe("provider_http_error");
    expect(redacted.status).toBe(502);
    // cause 被替换为仅保留 name 与已脱敏 message 的新 Error
    const safeCause = redacted.cause as Error;
    expect(safeCause).toBeInstanceOf(Error);
    expect(safeCause).not.toBe(cause);
    expect(safeCause.name).toBe("TypeError");
    expect(safeCause.message).not.toContain(secret);
    expect(safeCause.message).toContain(REDACTED);
  });

  it("顶层 message 与 cause.message 都含短密钥时，两者都被脱敏", () => {
    const secret = "ab";
    const cause = new Error("token=ab in body");
    const original = new AgentError("provider_protocol_error", "payload had ab inside", {
      cause,
    });

    const redacted = redactAgentError(original, secret);

    expect(redacted.message).toContain(REDACTED);
    expect(redacted.message).not.toContain("payload had ab inside");
    expect(redacted.category).toBe("provider_protocol_error");
    const safeCause = redacted.cause as Error;
    expect(safeCause.message).toContain(REDACTED);
    expect(safeCause.message).not.toContain("token=ab");
  });

  it("非 Error 的 cause 无法确定安全，直接丢弃", () => {
    const secret = "sk-supersecret-1234567890";
    const original = new AgentError("provider_protocol_error", "boom", {
      cause: { key: secret },
    });

    const redacted = redactAgentError(original, secret);

    expect(redacted.cause).toBeUndefined();
  });

  it("整个错误链（message 与 cause.message）均不含原始密钥", () => {
    const secret = "sk-supersecret-1234567890";
    const cause = new Error(`root cause with ${secret}`);
    const original = new AgentError("provider_http_error", `top with ${secret}`, {
      cause,
      status: 400,
    });

    const redacted = redactAgentError(original, secret);
    const serialized = `${redacted.message} ${(redacted.cause as Error).message}`;
    expect(serialized).not.toContain(secret);
  });
});
