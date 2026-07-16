# Retrieval Evaluation

## 目的

检索评测用于判断“正确规则章节是否进入 Agent 可读取的前 K 个候选”，不直接评价最终自然语言答案。评测集位于 `rulepacks/pathfinder-1e/evals/retrieval.jsonl`，每题保存问题和人工确认的相关父文档 ID。

## 当前基线

本地 PF1E 数据：2,140 篇父文档、29,629 个 500 字重叠子块，模型为 `BAAI/bge-small-zh-v1.5`。

| 检索方式 | Hit@5 | MRR |
| --- | ---: | ---: |
| 纯 BGE + Chroma | 78.8% | 0.638 |
| BGE + BM25 加权 RRF | 96.5% | 0.821 |

当前共 85 道人工标注题：77 道覆盖战斗、动作、状态、法术、技能、专长等核心主题，另有 8 道明确指定 FAQ、Unchained、Ultimate Combat、Player Companion 或 GMG 的可选资料题。后者用于防止“核心规则来源优先级”把用户明确询问的可选规则压出候选；8 道均命中前 5。

混合检索先以中文单字/双字词元和英文单词建立 BM25 排名，再与向量排名做加权 Reciprocal Rank Fusion，并把来源优先级作为轻量排序信号。参数在离线评测上选择：每路候选 50、RRF `k=60`、向量权重 1.0、BM25 权重 1.2。

当前仍有 3 道 Hit@5 漏召回：敏捷属性用途、全防御动作和火把照明。它们被保留为后续结构化章节切块或 reranker 的回归目标，没有通过向语料注入答案来“刷分”。这些指标只评价父文档是否进入 Agent 可读取的前 5，不代表最终自然语言答案准确率。

## 复现

```bash
npm run eval:retrieval:pf:vector
npm run eval:retrieval:pf
```

两条命令分别生成纯向量和混合检索报告；详细排名保存在 Git 忽略的 `data/pathfinder-1e/generated/` 中。
