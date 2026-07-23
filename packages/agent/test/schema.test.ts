import { describe, expect, it } from "vitest";
import { validateSchema, type JsonSchema } from "../src/core/schema.ts";

const schema: JsonSchema = {
  type: "object",
  properties: {
    query: { type: "string" },
    limit: { type: "integer", minimum: 1, maximum: 10 },
    ids: { type: "array", items: { type: "string" }, minItems: 1, maxItems: 3 },
  },
  required: ["query"],
  additionalProperties: false,
};

describe("validateSchema", () => {
  it("接受符合子集约束的对象", () => {
    expect(validateSchema(schema, { query: "q", limit: 3, ids: ["a"] })).toEqual([]);
  });

  it("可选字段可以缺省", () => {
    expect(validateSchema(schema, { query: "q" })).toEqual([]);
  });

  it("拒绝非对象", () => {
    expect(validateSchema(schema, "text")).toEqual(["$ 应为对象"]);
    expect(validateSchema(schema, null)).toEqual(["$ 应为对象"]);
    expect(validateSchema(schema, [1])).toEqual(["$ 应为对象"]);
  });

  it("检查必需字段", () => {
    expect(validateSchema(schema, {})).toEqual(["$ 缺少必需字段 query"]);
  });

  it("additionalProperties=false 时拒绝额外字段", () => {
    expect(validateSchema(schema, { query: "q", extra: 1 })).toEqual(["$ 不允许额外字段 extra"]);
  });

  it("检查字符串类型", () => {
    expect(validateSchema(schema, { query: 42 })).toEqual(["$.query 应为字符串"]);
  });

  it("检查整数与上下界", () => {
    expect(validateSchema(schema, { query: "q", limit: 1.5 })).toEqual(["$.limit 应为整数"]);
    expect(validateSchema(schema, { query: "q", limit: 0 })).toEqual(["$.limit 不能小于 1"]);
    expect(validateSchema(schema, { query: "q", limit: 11 })).toEqual(["$.limit 不能大于 10"]);
  });

  it("检查数组长度与元素类型", () => {
    expect(validateSchema(schema, { query: "q", ids: [] })).toEqual(["$.ids 至少需要 1 个元素"]);
    expect(validateSchema(schema, { query: "q", ids: ["a", "b", "c", "d"] }))
      .toEqual(["$.ids 最多允许 3 个元素"]);
    expect(validateSchema(schema, { query: "q", ids: ["a", 2] })).toEqual(["$.ids[1] 应为字符串"]);
    expect(validateSchema(schema, { query: "q", ids: "a" })).toEqual(["$.ids 应为数组"]);
  });

  it("汇总多个错误", () => {
    const errors = validateSchema(schema, { limit: 0, extra: true });
    expect(errors).toContain("$ 缺少必需字段 query");
    expect(errors).toContain("$.limit 不能小于 1");
    expect(errors).toContain("$ 不允许额外字段 extra");
  });
});
