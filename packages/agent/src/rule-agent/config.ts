import { AgentError } from "../core/errors.ts";
import type { ModelConfig } from "../core/provider.ts";

/**
 * 非敏感配置。凭据被刻意排除在外：
 * API Key 属于 RuleAgentCredentials，只在单次 turn 生命周期内传递。
 */
export interface RuleAgentConfig {
  /** 模型 Provider ID，默认 openai-compatible。 */
  provider: string;
  model: ModelConfig;
  baseUrl: string;
  retrievalBaseUrl: string;
  rulesetId: string;
}

/** 模型凭据。不进入 config、messages、session 或 events。 */
export interface RuleAgentCredentials {
  apiKey: string;
}

const DEFAULT_PROVIDER = "openai-compatible";
const DEFAULT_RETRIEVAL_BASE_URL = "http://127.0.0.1:8765";
const DEFAULT_RULESET_ID = "pathfinder-1e";

/**
 * 从环境变量加载非敏感配置。
 * 必需项：LLM_MODEL、LLM_BASE_URL。
 * 缺失时抛出 configuration_error，并列出缺失变量。
 */
export function loadRuleAgentConfig(env: NodeJS.ProcessEnv): RuleAgentConfig {
  const missing = ["LLM_MODEL", "LLM_BASE_URL"].filter((name) => !env[name]);
  if (missing.length > 0) {
    throw new AgentError(
      "configuration_error",
      `缺少必需的环境变量：${missing.join("、")}。请在 .env 中配置（参考 .env.example）。`,
    );
  }

  const model: ModelConfig = {
    id: env.LLM_MODEL as string,
    contextWindow: Number(env.LLM_CONTEXT_WINDOW ?? 128000),
    maxTokens: Number(env.LLM_MAX_TOKENS ?? 8192),
    reasoning: env.LLM_REASONING === "true",
  };

  return {
    provider: env.LLM_PROVIDER ?? DEFAULT_PROVIDER,
    model,
    baseUrl: (env.LLM_BASE_URL as string).replace(/\/$/, ""),
    retrievalBaseUrl: env.RETRIEVAL_BASE_URL ?? DEFAULT_RETRIEVAL_BASE_URL,
    rulesetId: env.RULESET_ID ?? DEFAULT_RULESET_ID,
  };
}

/**
 * 从环境变量加载模型凭据（LLM_API_KEY）。与 config 独立加载，
 * 调用方在每次 run 时传入，不落入任何长生命周期对象。
 */
export function loadRuleAgentCredentials(env: NodeJS.ProcessEnv): RuleAgentCredentials {
  const apiKey = env.LLM_API_KEY;
  if (!apiKey) {
    throw new AgentError(
      "configuration_error",
      "缺少必需的环境变量：LLM_API_KEY。请在 .env 中配置（参考 .env.example）。",
    );
  }
  return { apiKey };
}
