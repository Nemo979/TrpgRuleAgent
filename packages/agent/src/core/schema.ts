/**
 * 零依赖 JSON Schema 校验器。
 *
 * 只覆盖当前工具所需的子集：
 * - type: object / string / integer / array
 * - object: properties、required、additionalProperties=false
 * - integer: minimum、maximum
 * - array: items、minItems、maxItems
 *
 * 校验失败返回错误列表；调用方在存在错误时不得执行工具。
 */

export interface JsonSchema {
  type?: "object" | "string" | "integer" | "array";
  description?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  additionalProperties?: boolean;
  items?: JsonSchema;
  minimum?: number;
  maximum?: number;
  minItems?: number;
  maxItems?: number;
}

export function validateSchema(schema: JsonSchema, value: unknown, path = "$"): string[] {
  const errors: string[] = [];
  validate(schema, value, path, errors);
  return errors;
}

function validate(schema: JsonSchema, value: unknown, path: string, errors: string[]): void {
  switch (schema.type) {
    case "object":
      validateObject(schema, value, path, errors);
      return;
    case "string":
      if (typeof value !== "string") {
        errors.push(`${path} 应为字符串`);
      }
      return;
    case "integer":
      validateInteger(schema, value, path, errors);
      return;
    case "array":
      validateArray(schema, value, path, errors);
      return;
    default:
      // 未声明 type 的 schema 不做约束。
      return;
  }
}

function validateObject(schema: JsonSchema, value: unknown, path: string, errors: string[]): void {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    errors.push(`${path} 应为对象`);
    return;
  }
  const record = value as Record<string, unknown>;
  const properties = schema.properties ?? {};

  for (const key of schema.required ?? []) {
    if (!(key in record)) {
      errors.push(`${path} 缺少必需字段 ${key}`);
    }
  }

  for (const [key, propertyValue] of Object.entries(record)) {
    const propertySchema = properties[key];
    if (propertySchema === undefined) {
      if (schema.additionalProperties === false) {
        errors.push(`${path} 不允许额外字段 ${key}`);
      }
      continue;
    }
    validate(propertySchema, propertyValue, `${path}.${key}`, errors);
  }
}

function validateInteger(schema: JsonSchema, value: unknown, path: string, errors: string[]): void {
  if (typeof value !== "number" || !Number.isInteger(value)) {
    errors.push(`${path} 应为整数`);
    return;
  }
  if (schema.minimum !== undefined && value < schema.minimum) {
    errors.push(`${path} 不能小于 ${schema.minimum}`);
  }
  if (schema.maximum !== undefined && value > schema.maximum) {
    errors.push(`${path} 不能大于 ${schema.maximum}`);
  }
}

function validateArray(schema: JsonSchema, value: unknown, path: string, errors: string[]): void {
  if (!Array.isArray(value)) {
    errors.push(`${path} 应为数组`);
    return;
  }
  if (schema.minItems !== undefined && value.length < schema.minItems) {
    errors.push(`${path} 至少需要 ${schema.minItems} 个元素`);
  }
  if (schema.maxItems !== undefined && value.length > schema.maxItems) {
    errors.push(`${path} 最多允许 ${schema.maxItems} 个元素`);
  }
  if (schema.items !== undefined) {
    value.forEach((item, index) => {
      validate(schema.items as JsonSchema, item, `${path}[${index}]`, errors);
    });
  }
}
