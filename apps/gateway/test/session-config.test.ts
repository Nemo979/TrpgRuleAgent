import { describe, expect, it } from "vitest";
import { GatewayError } from "../src/errors.ts";
import { parseSessionCreateRequest, type SessionCreatePolicy } from "../src/session-config.ts";

const policy: SessionCreatePolicy = {
  providerIds: ["openai-compatible"],
  allowedRulesets: ["pathfinder-1e", "demo"],
  defaultRulesetId: "pathfinder-1e",
};

function valid(): Record<string, unknown> {
  return {
    provider: "openai-compatible",
    model: { id: "gpt-x", contextWindow: 128000, maxTokens: 8192, reasoning: false },
    baseUrl: "https://llm.example/v1",
  };
}

function code(body: Record<string, unknown>): string | undefined {
  try {
    parseSessionCreateRequest(body, policy);
    return undefined;
  } catch (error) {
    if (error instanceof GatewayError) {
      return error.code;
    }
    throw error;
  }
}

describe("parseSessionCreateRequest", () => {
  it("合法请求：返回归一化配置，rulesetId 缺省用默认值", () => {
    const config = parseSessionCreateRequest(valid(), policy);
    expect(config).toEqual({
      provider: "openai-compatible",
      model: { id: "gpt-x", contextWindow: 128000, maxTokens: 8192, reasoning: false },
      baseUrl: "https://llm.example/v1",
      rulesetId: "pathfinder-1e",
    });
  });

  it("baseUrl 尾部斜杠归一化；显式 rulesetId 生效", () => {
    const config = parseSessionCreateRequest(
      { provider: "openai-compatible", model: { id: "m" }, baseUrl: "https://llm.example/v1///", rulesetId: "demo" },
      policy,
    );
    expect(config.baseUrl).toBe("https://llm.example/v1");
    expect(config.rulesetId).toBe("demo");
  });

  it("空 body 拒绝：无法确定模型", () => {
    expect(code({})).toBe("invalid_request");
  });

  it("缺失必填项拒绝：provider / model / model.id / baseUrl", () => {
    for (const field of ["provider", "model", "baseUrl"]) {
      const body = valid();
      delete body[field];
      expect(code(body)).toBe("invalid_request");
    }
    expect(code({ ...valid(), model: {} })).toBe("invalid_request");
  });

  it("未注册 provider 拒绝", () => {
    expect(code({ ...valid(), provider: "anthropic" })).toBe("invalid_request");
  });

  it("未知字段拒绝：顶层与 model 内部", () => {
    expect(code({ ...valid(), extra: 1 })).toBe("invalid_request");
    expect(code({ ...valid(), model: { id: "m", temperature: 0.5 } })).toBe("invalid_request");
  });

  it("用户提供 retrievalBaseUrl 拒绝", () => {
    expect(code({ ...valid(), retrievalBaseUrl: "http://attacker.example" })).toBe("invalid_request");
  });

  it("类型与范围校验：非字符串、超长、非法整数、非布尔", () => {
    expect(code({ ...valid(), provider: 42 })).toBe("invalid_request");
    expect(code({ ...valid(), baseUrl: "" })).toBe("invalid_request");
    expect(code({ ...valid(), baseUrl: `https://llm.example/${"a".repeat(2000)}` })).toBe("invalid_request");
    expect(code({ ...valid(), model: { id: "m".repeat(201) } })).toBe("invalid_request");
    expect(code({ ...valid(), model: { id: "m", contextWindow: 0 } })).toBe("invalid_request");
    expect(code({ ...valid(), model: { id: "m", contextWindow: 1.5 } })).toBe("invalid_request");
    expect(code({ ...valid(), model: { id: "m", maxTokens: -1 } })).toBe("invalid_request");
    expect(code({ ...valid(), model: { id: "m", maxTokens: 10_000_001 } })).toBe("invalid_request");
    expect(code({ ...valid(), model: { id: "m", reasoning: "yes" } })).toBe("invalid_request");
    expect(code({ ...valid(), model: [] })).toBe("invalid_request");
  });

  it("rulesetId 不在允许集合拒绝（ruleset_not_allowed）", () => {
    expect(code({ ...valid(), rulesetId: "dnd-5e" })).toBe("ruleset_not_allowed");
  });
});
