import { describe, expect, it } from "vitest";
import type { RuleDocument } from "@trpg-rule-agent/rules-types";
import { CitationRegistry } from "../src/citations.ts";

const document: RuleDocument = {
  id: "doc-1",
  rulesetId: "pathfinder-1e",
  sourceId: "crb",
  sourceTitle: "核心规则书",
  title: "借机攻击",
  fullPath: "核心规则书 > 战斗 > 借机攻击",
  content: "规则正文",
  version: "1e",
  priority: 100,
  metadata: {},
};

describe("CitationRegistry", () => {
  it("为同一文档复用稳定编号", () => {
    const registry = new CitationRegistry();
    expect(registry.register(document).label).toBe("S1");
    expect(registry.register(document).label).toBe("S1");
    expect(registry.list()).toHaveLength(1);
  });
});
