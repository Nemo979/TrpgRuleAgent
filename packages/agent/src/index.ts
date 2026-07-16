import { Agent } from "@earendil-works/pi-agent-core";
import type { Api, Model } from "@earendil-works/pi-ai";
import type { RulesClient } from "@trpg-rule-agent/rules-client";
import { ToolBudget } from "./budget.ts";
import { CitationRegistry } from "./citations.ts";
import { createRuleTools } from "./tools.ts";

export { CitationRegistry } from "./citations.ts";

const SYSTEM_PROMPT = `你是 TrpgRuleAgent 的 Pathfinder 1E 规则助手。

工作要求：
1. 回答规则事实前必须调用 search_rules 检索证据。
2. search_rules 的摘要不能作为最终引用；必须调用 read_rules 阅读相关完整文档。
3. 如果一个问题包含触发条件、例外或交叉规则，可以改写查询继续搜索，但不要重复同一查询。
4. 最终规则结论必须使用 read_rules 分配的 [S1]、[S2] 等引用编号。
5. 只能引用工具实际返回的编号，禁止编造来源或引用。
6. 文档明确标记为演示数据时，必须说明它不能用于真实规则判断。
7. 证据不足或不同来源冲突时，应明确说明，不要靠常识补全。
8. 回答使用中文，先给结论，再解释依据和例外。`;

export interface CreateRuleAgentOptions {
  model: Model<Api>;
  apiKey: string;
  client: RulesClient;
  rulesetId: string;
}

export interface RuleAgentRuntime {
  agent: Agent;
  citations: CitationRegistry;
  resetTurnState(): void;
}

export function createRuleAgent(options: CreateRuleAgentOptions): RuleAgentRuntime {
  const citations = new CitationRegistry();
  let budget = new ToolBudget({ maxToolCalls: 8, maxSearchCalls: 3, maxDocumentsRead: 8 });
  const createTools = () => createRuleTools({
    client: options.client,
    rulesetId: options.rulesetId,
    citations,
    budget,
  });

  const agent = new Agent({
    initialState: {
      systemPrompt: SYSTEM_PROMPT,
      model: options.model,
      thinkingLevel: "low",
      tools: createTools(),
    },
    getApiKey: () => options.apiKey,
    toolExecution: "sequential",
  });

  return {
    agent,
    citations,
    resetTurnState() {
      citations.clear();
      budget = new ToolBudget({ maxToolCalls: 8, maxSearchCalls: 3, maxDocumentsRead: 8 });
      agent.state.tools = createTools();
    },
  };
}

export function createConfiguredModel(env: NodeJS.ProcessEnv): Model<Api> {
  const api = env.LLM_API ?? "openai-completions";
  const modelId = env.LLM_MODEL;
  const baseUrl = env.LLM_BASE_URL;
  if (!modelId || !baseUrl) {
    throw new Error("必须配置 LLM_MODEL 和 LLM_BASE_URL");
  }

  return {
    id: modelId,
    name: modelId,
    api,
    provider: env.LLM_PROVIDER ?? "custom-openai",
    baseUrl: baseUrl.replace(/\/$/, ""),
    reasoning: env.LLM_REASONING === "true",
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: Number(env.LLM_CONTEXT_WINDOW ?? 128000),
    maxTokens: Number(env.LLM_MAX_TOKENS ?? 8192),
  };
}
