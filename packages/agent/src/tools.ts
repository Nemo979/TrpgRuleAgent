import type { AgentTool } from "@earendil-works/pi-agent-core";
import { Type } from "typebox";
import type { RulesClient } from "@trpg-rule-agent/rules-client";
import type { RuleDocument, RuleSearchHit } from "@trpg-rule-agent/rules-types";
import { ToolBudget } from "./budget.ts";
import { CitationRegistry } from "./citations.ts";

const searchParameters = Type.Object({
  query: Type.String({ description: "用于规则库检索的独立、明确查询" }),
  limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 10 })),
  sourceIds: Type.Optional(Type.Array(Type.String(), { maxItems: 10 })),
});

const readParameters = Type.Object({
  ids: Type.Array(Type.String(), {
    minItems: 1,
    maxItems: 8,
    description: "search_rules 返回的规则文档 ID",
  }),
});

export interface SearchToolDetails {
  query: string;
  hits: RuleSearchHit[];
}

export interface ReadToolDetails {
  documents: RuleDocument[];
  citations: Array<{ id: string; label: string; fullPath: string }>;
}

export interface RuleToolsOptions {
  client: RulesClient;
  rulesetId: string;
  citations: CitationRegistry;
  budget: ToolBudget;
}

export function createRuleTools(options: RuleToolsOptions): AgentTool[] {
  const searchTool: AgentTool<typeof searchParameters, SearchToolDetails> = {
    name: "search_rules",
    label: "搜索 PF 规则",
    description: "按语义搜索 Pathfinder 规则。只返回候选摘要；需要引用或核对原文时必须继续调用 read_rules。",
    parameters: searchParameters,
    async execute(_toolCallId, params, signal) {
      options.budget.consumeSearch();
      const hits = await options.client.search(
        {
          query: params.query,
          rulesetId: options.rulesetId,
          limit: params.limit ?? 8,
          ...(params.sourceIds ? { sourceIds: params.sourceIds } : {}),
        },
        signal,
      );

      const text = hits.length === 0
        ? "没有找到匹配规则。请改写查询，或者明确告知用户当前证据不足。"
        : hits.map((hit, index) => [
            `${index + 1}. id=${hit.id}`,
            `标题：${hit.title}`,
            `路径：${hit.fullPath}`,
            `分数：${hit.score.toFixed(4)}`,
            `摘要：${hit.excerpt}`,
          ].join("\n")).join("\n\n");

      return {
        content: [{ type: "text", text }],
        details: { query: params.query, hits },
      };
    },
  };

  const readTool: AgentTool<typeof readParameters, ReadToolDetails> = {
    name: "read_rules",
    label: "读取完整 PF 规则",
    description: "根据搜索结果 ID 读取完整规则文档，并为每篇文档分配可验证的引用编号。",
    parameters: readParameters,
    async execute(_toolCallId, params, signal) {
      const uniqueIds = [...new Set(params.ids)];
      options.budget.consumeRead(uniqueIds.length);
      const documents = await options.client.read(
        { rulesetId: options.rulesetId, ids: uniqueIds },
        signal,
      );
      const registered = documents.map((document) => options.citations.register(document));
      const text = registered.map(({ label, document }) => [
        `[${label}] ${document.fullPath}`,
        `文档 ID：${document.id}`,
        document.content,
      ].join("\n")).join("\n\n---\n\n");

      return {
        content: [{ type: "text", text: text || "没有读取到规则文档。" }],
        details: {
          documents,
          citations: registered.map(({ label, document }) => ({
            id: document.id,
            label,
            fullPath: document.fullPath,
          })),
        },
      };
    },
  };

  return [searchTool, readTool];
}
