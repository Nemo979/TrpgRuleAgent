import type { ModelConfig } from "@trpg-rule-agent/agent";
import { GatewayError } from "./errors.ts";

/**
 * 会话级非敏感连接配置（BYOK：模型由用户在创建会话时指定）。
 * 刻意不包含：
 * - apiKey —— 凭据只能通过每次 turn 的 X-Model-Api-Key 头传入；
 * - retrievalBaseUrl —— 始终由服务端注入，用户不可覆盖。
 */
export interface SessionConnectionConfig {
  provider: string;
  model: ModelConfig;
  baseUrl: string;
  rulesetId: string;
}

/** 校验所需的服务端策略。 */
export interface SessionCreatePolicy {
  /** Provider Registry 中已注册的 Provider ID 集合。 */
  providerIds: string[];
  /** 服务端允许的规则集集合。 */
  allowedRulesets: string[];
  /** rulesetId 缺省时使用的默认规则集。 */
  defaultRulesetId: string;
}

const TOP_LEVEL_FIELDS = new Set(["provider", "model", "baseUrl", "rulesetId"]);
const MODEL_FIELDS = new Set(["id", "contextWindow", "maxTokens", "reasoning"]);

const MAX_STRING_LENGTH = 200;
const MAX_BASE_URL_LENGTH = 2000;
const MAX_TOKEN_LIMIT = 10_000_000;

function invalid(message: string): GatewayError {
  return new GatewayError(400, "invalid_request", message);
}

function requireString(value: unknown, field: string, maxLength: number): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw invalid(`${field} 必须是非空字符串`);
  }
  const trimmed = value.trim();
  if (trimmed.length > maxLength) {
    throw invalid(`${field} 超过 ${maxLength} 字符上限`);
  }
  return trimmed;
}

function optionalBoundedInt(value: unknown, field: string): number | undefined {
  if (value === undefined) {
    return undefined;
  }
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1 || value > MAX_TOKEN_LIMIT) {
    throw invalid(`${field} 必须是 1..${MAX_TOKEN_LIMIT} 的整数`);
  }
  return value;
}

/**
 * 解析并严格校验 POST /v1/sessions 请求体。
 * 未知字段（含 model 内部）、用户提供 retrievalBaseUrl、类型/范围错误
 * 一律 400；凭据字段由调用方先行通过 assertNoCredentialFields 拒绝。
 * 空 body 无法确定模型，同样拒绝。
 */
export function parseSessionCreateRequest(
  body: Record<string, unknown>,
  policy: SessionCreatePolicy,
): SessionConnectionConfig {
  if ("retrievalBaseUrl" in body) {
    throw invalid("retrievalBaseUrl 由服务端注入，不允许由客户端指定");
  }
  const unknown = Object.keys(body).filter((key) => !TOP_LEVEL_FIELDS.has(key));
  if (unknown.length > 0) {
    throw invalid(`不支持的字段：${unknown.join("、")}`);
  }

  const provider = requireString(body.provider, "provider", MAX_STRING_LENGTH);
  if (!policy.providerIds.includes(provider)) {
    throw invalid(
      `未注册的模型 Provider：${provider}。可用值：${policy.providerIds.join("、") || "(无)"}`,
    );
  }

  const modelRaw = body.model;
  if (modelRaw === null || typeof modelRaw !== "object" || Array.isArray(modelRaw)) {
    throw invalid("model 必须是对象");
  }
  const modelBody = modelRaw as Record<string, unknown>;
  const unknownModel = Object.keys(modelBody).filter((key) => !MODEL_FIELDS.has(key));
  if (unknownModel.length > 0) {
    throw invalid(`model 中不支持的字段：${unknownModel.join("、")}`);
  }
  const modelId = requireString(modelBody.id, "model.id", MAX_STRING_LENGTH);
  const contextWindow = optionalBoundedInt(modelBody.contextWindow, "model.contextWindow");
  const maxTokens = optionalBoundedInt(modelBody.maxTokens, "model.maxTokens");
  if (modelBody.reasoning !== undefined && typeof modelBody.reasoning !== "boolean") {
    throw invalid("model.reasoning 必须是布尔值");
  }
  const model: ModelConfig = {
    id: modelId,
    ...(contextWindow !== undefined ? { contextWindow } : {}),
    ...(maxTokens !== undefined ? { maxTokens } : {}),
    ...(modelBody.reasoning !== undefined ? { reasoning: modelBody.reasoning as boolean } : {}),
  };

  const baseUrl = requireString(body.baseUrl, "baseUrl", MAX_BASE_URL_LENGTH).replace(/\/+$/, "");

  let rulesetId = policy.defaultRulesetId;
  if (body.rulesetId !== undefined) {
    rulesetId = requireString(body.rulesetId, "rulesetId", MAX_STRING_LENGTH);
  }
  if (!policy.allowedRulesets.includes(rulesetId)) {
    throw new GatewayError(
      400,
      "ruleset_not_allowed",
      `规则集不在允许范围：${rulesetId}。可用值：${policy.allowedRulesets.join("、")}`,
    );
  }

  return { provider, model, baseUrl, rulesetId };
}
