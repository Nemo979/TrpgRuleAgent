# Pathfinder 1E Evaluation Set

`retrieval.jsonl` 是父文档召回的人工审核集，当前包含 85 道题。当前字段：

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
