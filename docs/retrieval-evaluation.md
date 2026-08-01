# Retrieval Evaluation

## 目的

检索评测用于判断“正确规则章节是否进入 Agent 可读取的前 K 个候选”，不直接评价最终自然语言答案。长期评测集位于 `rulepacks/pathfinder-1e/evals/retrieval.jsonl`；结构化切分专项集位于 `structured-retrieval.jsonl`。每题保存问题和人工确认的相关父文档 ID。

## 当前基线

当前发布的 PF1E 数据：2,498 篇结构化父文档、33,585 个检索子块，模型为 `BAAI/bge-small-zh-v1.5`。CHM 标题拆成语义父文档，表格按“表头 + 数据行”生成结构块，同时用 `legacyParentId` 保持旧评测标注兼容。

| 检索方式 | Hit@5 | MRR |
| --- | ---: | ---: |
| 长期集：BGE + BM25 加权 RRF（85 题） | 100% | 0.8676 |
| 结构化专项集：旧父文档切分（30 题） | 100% | 0.8122 |
| 结构化专项集：当前结构化切分（30 题） | 100% | 0.9361 |

当前共 85 道人工标注题：77 道覆盖战斗、动作、状态、法术、技能、专长等核心主题，另有 8 道明确指定 FAQ、Unchained、Ultimate Combat、Player Companion 或 GMG 的可选资料题。后者用于防止“核心规则来源优先级”把用户明确询问的可选规则压出候选；8 道均命中前 5。

混合检索先以中文单字/双字词元和英文单词建立 BM25 排名，再与向量排名做加权 Reciprocal Rank Fusion，并把来源优先级作为轻量排序信号。参数在离线评测上选择：每路候选 50、RRF `k=60`、向量权重 1.0、BM25 权重 1.2。

原先漏召回的敏捷属性用途、全防御动作和火把照明均已进入前 5。发布前另以 MiMo 对 4 组、6 轮真实问题进行答案级抽样，检查事实、来源映射、错误事件和多轮追问，最终 6/6 通过。检索指标仍只评价父文档是否进入 Agent 可读取的前 5，不等同于完整答案准确率。

## 复现

```bash
npm run eval:retrieval:pf:vector
npm run eval:retrieval:pf
npm run eval:retrieval:pf:structured
```

两条命令分别生成纯向量和混合检索报告；详细排名保存在 Git 忽略的 `data/pathfinder-1e/generated/` 中。
