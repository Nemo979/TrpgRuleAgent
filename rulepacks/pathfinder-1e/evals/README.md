# Pathfinder 1E Evaluation Set

`retrieval.jsonl` 是父文档召回的人工审核集，当前包含 85 道题。当前字段：

`structured-retrieval.jsonl` 是结构化 CHM 切分的发布前专项集，包含表格行、
核心规则子章节和大型职业变体目录三类高风险用例。它与常规集分开维护，便于
比较旧父文档切分和结构化候选库，而不会悄悄改变长期基线的题目组成。

`answer-cases.jsonl` 是使用真实模型运行的多轮答案验收集。每一轮声明可接受的
事实表达和必须引用的父文档；验收器同时检查回答内容、来源映射、错误事件和多轮
上下文。该集合用于发布前抽样，不替代确定性的检索评测。

```json
{
  "id": "aao-trigger",
  "query": "什么时候会触发借机攻击？",
  "relevantIds": ["pf1e-5e20d1d4e7ee"]
}
```

- `id`：稳定用例 ID。
- `query`：用户自然语言问题。
- `relevantIds`：至少一篇能够直接支持回答的完整父文档 ID。

运行 `npm run eval:retrieval:pf:vector` 生成纯向量基线，运行 `npm run eval:retrieval:pf` 验证默认混合检索。当前指标为 Hit@5 和 MRR。

新增用例时必须人工阅读父文档后标注，不能根据当前检索结果反推相关 ID。后续答案级评测另行增加 `requiredFacts`、`forbiddenFacts`、引用有效率、无依据结论率、工具调用次数、延迟和 Token 消耗。
