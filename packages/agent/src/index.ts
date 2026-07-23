// Agent Core：业务无关的消息模型、事件、Provider 接口与 Agent Loop。
export * from "./core/index.ts";

// Providers：首版内置 OpenAI-compatible Chat Completions 流式 Provider。
export {
  OpenAICompatibleProvider,
  createDefaultProviderRegistry,
  parseSseStream,
} from "./providers/index.ts";

// TRPG Rule Agent：规则工具、预算、引用与配置。
export { ToolBudget, type ToolBudgetLimits } from "./rule-agent/budget.ts";
export { CitationRegistry, type RegisteredCitation } from "./rule-agent/citations.ts";
export {
  loadRuleAgentConfig,
  loadRuleAgentCredentials,
  type RuleAgentConfig,
  type RuleAgentCredentials,
} from "./rule-agent/config.ts";
export {
  createRuleTools,
  type ReadToolDetails,
  type ReadToolParams,
  type RuleToolsOptions,
  type SearchToolDetails,
  type SearchToolParams,
} from "./rule-agent/tools.ts";
export {
  createRuleAgent,
  type CreateRuleAgentOptions,
  type RuleAgentRuntime,
} from "./rule-agent/index.ts";
