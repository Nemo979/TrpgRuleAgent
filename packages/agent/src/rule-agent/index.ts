import type { RulesClient } from "@trpg-rule-agent/rules-client";
import type { AgentEvent } from "../core/events.ts";
import type { ModelProvider, ProviderRegistry } from "../core/provider.ts";
import { AgentRuntime } from "../core/runtime.ts";
import { ToolRegistry } from "../core/tools.ts";
import { createDefaultProviderRegistry } from "../providers/index.ts";
import { ToolBudget } from "./budget.ts";
import { CitationRegistry } from "./citations.ts";
import type { RuleAgentConfig } from "./config.ts";
import { createRuleTools } from "./tools.ts";

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

/** 与预算限制匹配：1 次起始调用 + 最多 8 次工具轮 + 收尾回答，留少量余量。 */
const MAX_MODEL_TURNS = 12;

export interface CreateRuleAgentOptions {
  config: RuleAgentConfig;
  client: RulesClient;
  /** 测试或扩展时可直接注入 Provider；默认按 config.provider 从 Registry 创建。 */
  provider?: ModelProvider;
  registry?: ProviderRegistry;
}

export interface RuleAgentRuntime {
  runtime: AgentRuntime;
  citations: CitationRegistry;
  run(input: string, signal?: AbortSignal): AsyncIterable<AgentEvent>;
  /** 每个用户问题前重置当轮预算和引用。 */
  resetTurnState(): void;
}

export function createRuleAgent(options: CreateRuleAgentOptions): RuleAgentRuntime {
  const registry = options.registry ?? createDefaultProviderRegistry();
  const provider = options.provider ?? registry.create(options.config.provider);

  const citations = new CitationRegistry();
  const budget = new ToolBudget({ maxToolCalls: 8, maxSearchCalls: 3, maxDocumentsRead: 8 });

  const tools = new ToolRegistry();
  for (const tool of createRuleTools({
    client: options.client,
    rulesetId: options.config.rulesetId,
    citations,
    budget,
  })) {
    tools.register(tool);
  }

  const runtime = new AgentRuntime({
    provider,
    model: options.config.model,
    baseUrl: options.config.baseUrl,
    getApiKey: () => options.config.apiKey,
    systemPrompt: SYSTEM_PROMPT,
    tools,
    maxModelTurns: MAX_MODEL_TURNS,
  });

  return {
    runtime,
    citations,
    run: (input, signal) => runtime.run(input, signal),
    resetTurnState() {
      citations.clear();
      budget.reset();
    },
  };
}
