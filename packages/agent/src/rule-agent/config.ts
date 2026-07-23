import { AgentError } from "../core/errors.ts";
import type { ModelConfig } from "../core/provider.ts";

export interface RuleAgentConfig {
  /** 模型 Provider ID，默认 openai-compatible。 */
  provider: string;
  model: ModelConfig;
  baseUrl: string;
  apiKey: string;
  retrievalBaseUrl: string;
  rulesetId: string;
}

const DEFAULT_PROVIDER = "openai-compatible";
const DEFAULT_RETRIEVAL_BASE_URL = "http://127.0.0.1:8765";
const DEFAULT_RULESET_ID = "pathfinder-1e";

/**
 * 从环境变量加载配置。
 * 必需项：LLM_MODEL、LLM_BASE_URL、LLM_API_KEY。
 * 缺失时抛出 configuration_error，并列出缺失变量。
 */
export function loadRuleAgentConfig(env: NodeJS.ProcessEnv): RuleAgentConfig {
  const missing = ["LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY"].filter((name) => !env[name]);
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
    apiKey: env.LLM_API_KEY as string,
    retrievalBaseUrl: env.RETRIEVAL_BASE_URL ?? DEFAULT_RETRIEVAL_BASE_URL,
    rulesetId: env.RULESET_ID ?? DEFAULT_RULESET_ID,
  };
}
