# 受限多问题拆解与路由

V2.3 Stage 2 在现有串行 Agent Loop 前增加纯代码 Route Decision 和 Query Decomposition。实现位于
`services/app-python/src/trpg_app/query_decomposition.py`，不调用 LLM Router、Planner 或并行 Agent。

## 行为边界

- `simple`：保持现有 Agent Loop，不生成子问题，也不增加模型调用。
- `compound`：识别显式独立问句或多个构筑目标，最多生成 4 个子问题。
- `complex`：仅在同时具备明确目标、约束、多个选择域和成长阶段时标记；明确出现先决依赖或条件
  分支时设置 `need_planner`。只有独立的 `enable_complex_planner` 开关开启后才执行 Stage 3。
- 低置信表达回退 `simple`，避免误拆。
- `enable_query_decomposition` 默认关闭；关闭后不改变 Prompt、预算和执行路径。

Route Decision 固定为版本 1，输出 intent、complexity、need_decomposition、need_planner、domains 和
reason_code。子问题只继承用户明确表达的目标，每项包含 id、question、domain 和 depends_on；这些
结构是检索计划，不是规则证据。

## 执行与预算

命中拆解后，服务端按子问题顺序确定性执行一组受限 `search_rules` / `read_rules`，随后只调用一次
MiMo 合成最终答案；不让模型自行分配搜索额度。每个子问题最多读取 4 篇，回答证据文档上限按
子问题数扩展、最多 16 篇，同时继续受全局 6 次搜索、24 篇文档和 Evidence Token 硬上限约束。
`simple` 路径不注入路由元数据，保持原系统提示和 Agent Loop。

每个子问题拥有独立 Evidence 文档/Token 配额和来源 ID 集合，不回收给其他子问题；所有子问题仍受
本轮 `EvidenceBudget` 的搜索数、文档数和 Evidence Token 全局硬上限约束。同一文档只注册和计费
一次，但允许作为多个子问题的共同来源。

## 可观测性

Turn metrics 新增：

- routing latency；
- route complexity、reason code 和 domain count；
- decomposition question count；
- 已搜索覆盖的子问题数；
- 已取得来源的子问题数。

日志仍不记录用户问题、答案或规则正文，只保留查询摘要哈希和聚合字段。

## 本地验收

确定性专项集位于 `rulepacks/pathfinder-1e/evals/query-decomposition-cases.jsonl`，运行：

```bash
npm run eval:decomposition:pf
```

2026-08-10 本地结果：12/12；单问题误拆率 0%；目标域覆盖率 100%；平均子问题数 1.5833；最大
子问题数 4；当前本地运行的路由耗时 mean 0.1212 ms、p95 0.9491 ms。Stage 3 接入后 Python App
165 项测试通过；Stage 2 的最多 4 子问题边界保持不变。

经明确授权完成真实 MiMo 验收：3 个纯合成 Compound/Complex 问题的固定路径为 2/3，受限拆解为
3/3；通过率由 66.67% 提升到 100%，无依据率由 33.33% 降到 0%，最大工具调用由 7 增至 8，仍低于
12 次评测预算。开启拆解开关后的既有 PF1E 6 轮回归为 6/6，最大工具调用 2、无依据率 0%。原始
模型问答、规则正文和完整报告只保存在 `/private/tmp`，未提交仓库。
