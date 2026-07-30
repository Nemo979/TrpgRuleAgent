import { afterEach, describe, expect, it, vi } from "vitest";
import { RulesClient } from "@trpg-rule-agent/rules-client";
import type { RuleDocument, RuleSearchHit } from "@trpg-rule-agent/rules-types";
import { ToolBudget } from "../src/rule-agent/budget.ts";
import { CitationRegistry } from "../src/rule-agent/citations.ts";
import { createRuleTools } from "../src/rule-agent/tools.ts";

const searchHit: RuleSearchHit = {
  id: "pf1e-combat",
  rulesetId: "pathfinder-1e",
  sourceId: "crb",
  sourceTitle: "核心规则书",
  title: "战斗中的数据计算",
  fullPath: "核心规则书 > 战斗 > 战斗中的数据计算",
  excerpt: "离开威胁范围通常会引发借机攻击。",
  score: 0.95,
  version: "1e",
  metadata: {},
};

const document: RuleDocument = {
  ...searchHit,
  content: "离开威胁范围通常会引发借机攻击，5尺快步和撤退动作属于例外。",
  priority: 100,
};

function createTools() {
  const citations = new CitationRegistry();
  const budget = new ToolBudget({
    maxToolCalls: 8,
    maxSearchCalls: 3,
    maxDocumentsRead: 8,
  });
  const [search, read] = createRuleTools({
    client: new RulesClient("http://127.0.0.1:8765"),
    rulesetId: "pathfinder-1e",
    citations,
    budget,
  });
  return { search, read, citations, budget };
}

describe("rule tools integration", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("搜索候选后读取完整父文档并注册稳定引用", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string, _init: RequestInit) => {
      if (url.endsWith("/search")) {
        return new Response(JSON.stringify({ data: [searchHit] }), { status: 200 });
      }
      if (url.endsWith("/documents/read")) {
        return new Response(JSON.stringify({ data: [document] }), { status: 200 });
      }
      return new Response(JSON.stringify({ error: "not found" }), { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const { search, read, citations } = createTools();

    const searchResult = await search.execute(
      { query: "什么时候触发借机攻击", limit: 5 },
      { toolCallId: "search-1" },
    );
    const readResult = await read.execute(
      { ids: [searchHit.id, searchHit.id] },
      { toolCallId: "read-1" },
    );

    expect(searchResult.content[0]).toMatchObject({
      type: "text",
      text: expect.stringContaining("pf1e-combat"),
    });
    expect(readResult.content[0]).toMatchObject({
      type: "text",
      text: expect.stringContaining("[S1]"),
    });
    expect(citations.formatSources()).toBe(`[S1] ${document.fullPath}`);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://127.0.0.1:8765/search",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          query: "什么时候触发借机攻击",
          rulesetId: "pathfinder-1e",
          limit: 5,
        }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:8765/documents/read",
      expect.objectContaining({
        body: JSON.stringify({
          rulesetId: "pathfinder-1e",
          ids: [searchHit.id],
        }),
      }),
    );
  });

  it("超过每轮搜索预算时拒绝继续调用服务", async () => {
    const fetchMock = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify({ data: [] }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { search } = createTools();

    for (let index = 0; index < 3; index += 1) {
      await search.execute({ query: `query-${index}` }, { toolCallId: `search-${index}` });
    }

    await expect(search.execute({ query: "too-many" }, { toolCallId: "search-4" }))
      .rejects.toThrow("本轮最多允许搜索 3 次");
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("预算重置后可以继续搜索（新一轮用户问题）", async () => {
    const fetchMock = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify({ data: [] }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { search, budget } = createTools();

    for (let index = 0; index < 3; index += 1) {
      await search.execute({ query: `query-${index}` }, { toolCallId: `search-${index}` });
    }
    budget.reset();
    await expect(search.execute({ query: "next-turn" }, { toolCallId: "search-next" }))
      .resolves.toBeDefined();
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("AbortSignal 透传给规则客户端请求", async () => {
    let requestSignal: AbortSignal | undefined;
    const fetchMock = vi.fn().mockImplementation(
      async (_url: string, init: RequestInit) => {
        requestSignal = init.signal ?? undefined;
        return await new Promise<Response>((_resolve, reject) => {
          requestSignal?.addEventListener(
            "abort",
            () => reject(new DOMException("aborted", "AbortError")),
            { once: true },
          );
        });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const { search } = createTools();
    const controller = new AbortController();

    const pending = search.execute(
      { query: "q" },
      { toolCallId: "s", signal: controller.signal },
    );
    controller.abort();

    await expect(pending).rejects.toMatchObject({
      message: "检索请求已取消",
      status: 499,
    });
    expect(requestSignal).toBeInstanceOf(AbortSignal);
    expect(requestSignal?.aborted).toBe(true);
  });
});
