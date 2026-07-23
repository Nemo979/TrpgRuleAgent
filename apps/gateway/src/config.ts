import { GatewayError } from "./errors.ts";

/**
 * Gateway 非敏感配置。
 * 两条刻意的缺席：
 * - 不含任何模型 API Key：凭据只能通过每次 turn 请求的 X-Model-Api-Key 头进入，
 *   并随该次请求的生命周期结束而消失；
 * - 不含全局模型配置（provider/model/baseUrl）：BYOK 模式下模型连接配置
 *   由用户在创建会话时提交，服务端只提供白名单与默认值。
 */
export interface GatewayConfig {
  host: string;
  port: number;
  /** 会话空闲 TTL（毫秒），默认 30 分钟。 */
  sessionTtlMs: number;
  /** CORS Origin 白名单；为空表示不允许任何跨域来源。 */
  allowedOrigins: string[];
  /** 模型 Base URL 前缀白名单；空数组配合 allowLocalhostModel=false 时拒绝一切。 */
  modelBaseUrlAllowlist: string[];
  /** 本地开发开关：允许 http://localhost / 127.0.0.1 模型端点，默认关闭。 */
  allowLocalhostModel: boolean;
  /** 请求体上限（字节）。 */
  maxBodyBytes: number;
  /** 单次 turn 输入字符数上限。 */
  maxInputChars: number;
  /** 单个来源在窗口内允许的请求数（健康检查与 OPTIONS 除外）。 */
  rateLimitMaxRequests: number;
  /** 请求限流窗口（毫秒）。 */
  rateLimitWindowMs: number;
  /** 检索服务地址：始终由服务端注入，客户端不可覆盖。 */
  retrievalBaseUrl: string;
  /** 可选的云端检索服务鉴权令牌；仅用于服务端到服务端请求。 */
  retrievalApiKey?: string;
  /** 允许客户端选择的规则集集合。 */
  allowedRulesets: string[];
  /** rulesetId 缺省时的默认规则集（必须属于 allowedRulesets）。 */
  defaultRulesetId: string;
}

export const DEFAULT_SESSION_TTL_MS = 30 * 60 * 1000;
export const DEFAULT_MAX_BODY_BYTES = 16 * 1024;
export const DEFAULT_MAX_INPUT_CHARS = 8000;
export const DEFAULT_RATE_LIMIT_MAX_REQUESTS = 120;
export const DEFAULT_RATE_LIMIT_WINDOW_MS = 60_000;
const DEFAULT_RETRIEVAL_BASE_URL = "http://127.0.0.1:8765";
const DEFAULT_RULESET_ID = "pathfinder-1e";

function parseList(value: string | undefined): string[] {
  if (!value) {
    return [];
  }
  return value
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function parsePositiveInt(value: string | undefined, fallback: number, name: string): number {
  if (value === undefined || value === "") {
    return fallback;
  }
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new GatewayError(500, "configuration_error", `环境变量 ${name} 必须是正整数`);
  }
  return parsed;
}

/**
 * 从环境变量加载 Gateway 配置。
 * 凭据类变量（LLM_API_KEY）与全局模型变量（LLM_MODEL/LLM_BASE_URL）在此被刻意忽略。
 */
export function loadGatewayConfig(env: NodeJS.ProcessEnv): GatewayConfig {
  const defaultRulesetId = env.RULESET_ID ?? DEFAULT_RULESET_ID;
  const allowedRulesets = parseList(env.GATEWAY_ALLOWED_RULESETS);
  if (allowedRulesets.length === 0) {
    allowedRulesets.push(defaultRulesetId);
  }
  if (!allowedRulesets.includes(defaultRulesetId)) {
    throw new GatewayError(
      500,
      "configuration_error",
      `默认规则集 ${defaultRulesetId} 不在 GATEWAY_ALLOWED_RULESETS 中`,
    );
  }

  return {
    host: env.GATEWAY_HOST ?? "127.0.0.1",
    port: parsePositiveInt(env.GATEWAY_PORT, 8787, "GATEWAY_PORT"),
    sessionTtlMs: parsePositiveInt(
      env.GATEWAY_SESSION_TTL_MS,
      DEFAULT_SESSION_TTL_MS,
      "GATEWAY_SESSION_TTL_MS",
    ),
    allowedOrigins: parseList(env.GATEWAY_ALLOWED_ORIGINS),
    modelBaseUrlAllowlist: parseList(env.GATEWAY_MODEL_BASE_URL_ALLOWLIST),
    allowLocalhostModel: env.GATEWAY_ALLOW_LOCALHOST_MODEL === "true",
    maxBodyBytes: DEFAULT_MAX_BODY_BYTES,
    maxInputChars: DEFAULT_MAX_INPUT_CHARS,
    rateLimitMaxRequests: parsePositiveInt(
      env.GATEWAY_RATE_LIMIT_MAX_REQUESTS,
      DEFAULT_RATE_LIMIT_MAX_REQUESTS,
      "GATEWAY_RATE_LIMIT_MAX_REQUESTS",
    ),
    rateLimitWindowMs: parsePositiveInt(
      env.GATEWAY_RATE_LIMIT_WINDOW_MS,
      DEFAULT_RATE_LIMIT_WINDOW_MS,
      "GATEWAY_RATE_LIMIT_WINDOW_MS",
    ),
    retrievalBaseUrl: env.RETRIEVAL_BASE_URL ?? DEFAULT_RETRIEVAL_BASE_URL,
    ...(env.RETRIEVAL_API_KEY ? { retrievalApiKey: env.RETRIEVAL_API_KEY } : {}),
    allowedRulesets,
    defaultRulesetId,
  };
}
