# 动态 Evidence Budget

## 范围

V2.3 Stage 1 在现有 `EvidenceBudget`、`ContextBudget` 和串行工具循环上增加一层纯代码策略，
不增加 Router、Planner 或模型调用。实现位于 `services/app-python/src/trpg_app/evidence_policy.py`。

`enable_dynamic_evidence_budget` 默认关闭。关闭时继续使用 Stage 0 的固定上限；开启时按当前问题
意图和本轮可用 Context 生成 `EvidenceBudgetProfile`：

- `RULE_FACT`：最多 3 次搜索、4 个答案来源、12,000 Evidence token；
- 指代型 `RULE_FACT` 追问：最多 5 次搜索、6 个答案来源、24,000 Evidence token；
- `PROCEDURE`：最多 4 次搜索、6 个答案来源、24,000 Evidence token；
- `COMPARE_OPTIONS`：最多 5 次搜索、8 个答案来源、32,000 Evidence token；
- `BUILD_ADVICE`：最多 6 次搜索、8 个答案来源、40,000 Evidence token。

所有 token 档位都会进一步受 `ContextBudget.evidence_tokens` 限制。未知意图回退到固定预算。

## 多主题保护

Compare 和 Build profile 会为三个主题预留配额。搜索候选通过确定性关键词和条目标题归属主题；
证据接纳时至少为尚未覆盖的其他主题保留一个文档位置。某主题已有证据后，它未使用的 token
配额可以由其他已覆盖主题回收，但任何单一主题不能在其他主题尚未覆盖时占满全部文档容量。

该归属只影响预算，不构成规则事实，也不进入最终答案。指标只记录 profile 上限、策略版本和
已覆盖主题数量，不记录主题正文、问题或规则正文。

多主题模式会为服务端恢复预留最后一次搜索。模型连续搜索不读取或过早结束时，服务端复用已有
`QueryPlan`，按搜索批次轮询读取高位候选；Compare 缺一侧时使用规范规则标题补查。PF1E 的
“防御式战斗”位于“攻击（Attack）”章节，策略只做术语规范化，不把该映射当作最终规则事实。
指代型事实追问会优先复用上一问提取出的短规则条目名，并在模型无证据直接结束时执行一次
受同一硬预算约束的 search/read 恢复。

## 专项评测

`rulepacks/pathfinder-1e/evals/dynamic-evidence-cases.jsonl` 包含一个连续 5 轮合成会话：

- 4 轮法师构筑/兼职追问；
- 第 5 轮全防御与防御式战斗的双来源比较。

`requiredSourceGroups` 要求每个关键主题分别命中至少一个 gold 来源；只命中任意一侧不再算通过。

固定预算基线：

```bash
TRPG_TURN_METRICS_PATH=data/pathfinder-1e/generated/evidence-fixed-metrics.jsonl \
  npm run eval:answer:pf:evidence-fixed
```

动态预算候选：

```bash
TRPG_TURN_METRICS_PATH=data/pathfinder-1e/generated/evidence-dynamic-metrics.jsonl \
  npm run eval:answer:pf:evidence-dynamic
```

真实模型 A/B 会发送新增 Build/Compare 问题和读取的 PF1E 证据，必须取得覆盖该题集的明确外发授权。
本地诊断报告不得提交；仓库只记录聚合结论。

## 当前结果

截至 2026-08-10：

- 纯代码策略、Feature Flag、主题配额、回退路径和安全指标已落地；
- PF1E 85 题检索保持 Hit@5 90.6%、MRR 0.7351；
- PF1E 30 题结构化检索保持 Hit@5 83.3%、MRR 0.6750；
- GSS 22 题检索保持 Hit@5 100%、MRR 0.9773；
- 经明确授权完成 MiMo 真实 A/B：固定预算 5/5，动态预算 5/5，来源命中 100%，无依据率 0%；
- 动态组的完整运行前 4 轮与同一合成历史下的 Compare 定向复验合并计分；原始模型报告只保存在
  本地临时目录，仓库不提交问题、回答或规则正文；
- 固定/动态专项集的 mean 分别为：总延迟 146.54/86.68 秒、prompt token
  29,321.2/38,985.4、搜索 2.8/4.0、读取 4.8/5.0、Evidence token 8,396.8/6,212.2；
- 正确 release 结构化库上的 PF1E 6 轮动态回归，经两个失败追问的同历史定向复验后合并为
  6/6、来源命中 100%、无依据率 0%；
- 6 轮动态指标 mean/p95：prompt token 10,924.5/15,639、总延迟 40.12/53.91 秒、
  搜索 1.0/1.0、读取 1.17/2.0；相对当前固定基线 prompt mean 11,528、延迟 mean 57.26 秒
  均未增加，满足简单事实题不超过 10% 的门槛。

Stage 1 发布门已满足。Feature Flag 仍默认关闭，是否逐步开启由后续发布流程决定。
