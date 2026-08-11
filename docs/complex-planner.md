# 受限 Complex Planner

V2.3 Stage 3 在 Stage 2 路由与拆解之上增加确定性的受限计划和串行 Executor。实现位于
`services/app-python/src/trpg_app/complex_planner.py`，正式链路接入位于 `trpg_app.chat`。

## 边界

- 仅对同时具备 Complex 路由和明确依赖/条件语言的任务设置 `need_planner`；独立的多域构筑仍走
  Stage 2，简单问题保持原 Agent Loop。
- 计划最多 6 个任务，只允许 `rule_research`、`compare`、`selection`、`progression` 和固定领域。
- Validator 拒绝 URL、工具名、模型名、系统命令、未知依赖、重复 ID、非拓扑顺序、环和超限任务。
- 计划由确定性路由结果生成，不增加一次 Planner 模型调用；模型只负责最终答案合成。
- `enable_complex_planner` 默认关闭。Schema 构建或验证失败时保留 Stage 2 拆解路径。

## Fact Ledger 集成边界

当前 Planner 路径直接调用 PF1E 原型 Ledger。§8.7 整改后，Planner 只依赖通用 Ledger/Adapter
接口：根据当前 `LibraryManifest` 选择 Adapter，把已注册 Evidence 转换为 FactRecord，验证带稳定
claim ID 的 AnswerDraft，并只修补失败路径。Planner 不导入 PF1E Parser 或 Validator；没有匹配
Adapter 时记录降级原因并沿用原回答路径。

## 串行执行

Executor 按拓扑顺序复用正式 `search_rules` / `read_rules`：

- 每个取证任务拥有独立 Evidence 配额和来源归属；共享来源可登记到多个任务，但正文只计费一次；
- 每个任务最长 30 秒，全局最长 90 秒，并继续受模型请求超时和全局搜索/文档/Token 预算约束；
- 失败任务只生成 `status`、`missing_fields`、来源 ID 和安全摘要，不修改 `ConversationState`；
- 最终生成器接收用户目标、显式状态、受限计划、结构化任务结果和已注册 Evidence，不接收工具历史；
- 回答提示强制区分规则事实、推导、建议、收益损失、适用条件、缺失信息和来源。

## 合成契约

真实 A/B 暴露的问题集中在最终合成而非任务执行，因此增加
`services/app-python/src/trpg_app/synthesis_contract.py`：

- 纯代码识别条件分支、等级算术、专长资格、法术成长和装备数值五类高风险主张；
- “指定法术环级”等未提供的关键字段标为 `missing_input`，强制按目标分支回答，禁止擅自补值；
- 每类检查只记录需要哪些 Evidence 领域及当前是否有来源，不把检查或就绪状态当作规则事实；
- 专长必须区分通用、种族和职业奖励可选范围；等级必须区分角色/职业/施法者等级与法术环级；
- 法术和装备的名称、环级、价格、伤害、加值及叠加关系没有精确条目时必须省略或声明证据不足；
- 合成契约最多 6 项，不增加模型调用；契约无效时沿用现有 Planner fallback。

## 可观测性

Turn metrics 记录 `complexPlannerEnabled`、`complexPlannerUsed`、`complexPlannerFallback`、
`plannerVersion`、任务总数/完成数/失败数、`plannerSeconds` 和 `executorSeconds`，并新增合成契约
版本、检查数、未决输入数和缺证据检查数。日志仍不记录问题、答案、计划查询正文或规则正文。

## 本地验收

2026-08-11：

- 专长链查询修复后，8 个本地证据探针恢复为 Hit@5 8/8、MRR 0.875；
- 10 个结构案例分类 10/10：4 个保持 Stage 2，6 个生成合法 Planner v1 计划；
- 六题正式本地规则库生产路径来源组 6/6，所有计划任务完成；最终生成器使用本地假实现，未调用外部模型；
- 合成契约结构评测 10/10：4 个保持 Stage 2，6 个 Planner 候选均生成有效契约，下一动作收敛为
  `run_local_synthesis_regression`；
- Python App 167/167、Retrieval 64/64、Vitest 268/268、TypeScript 和 Web 生产构建通过。

## 真实 A/B

经明确授权，2026-08-11 使用同一组六个完全合成问题和正式检索证据完成真实 MiMo A/B：

- Stage 2 与 Planner 的自动关键词/来源门均为 5/6，不支持来源率均为 0%；
- Planner 6/6 实际启用，32/32 任务完成，0 fallback；
- 严格任务完成率没有提升，保持 0/6 → 0/6；
- 工具调用 46 → 52，平均端到端时延 93.89s → 119.12s，总模型 token 243,239 → 280,942；
- 局部目标覆盖改善不足以抵消职业奖励专长、法术、装备和等级算术错误。

因此真实质量门未通过，`enable_complex_planner` 继续默认关闭。

合成契约完成后，经再次明确授权于同日重新执行同六题 A/B：契约在 6/6 题启用，26 项检查均有
Evidence 就绪，正确识别 1 个未决目标环级，32/32 Planner 任务完成且 0 fallback；但自动门仍为
5/6 → 5/6，严格任务完成率仍为 0/6 → 0/6。相对同轮 Stage 2，工具调用 46 → 52、平均端到端
时延 87.81s → 111.51s、模型总 token 244,592 → 290,132。MiMo 仍违反等级求和、职业奖励专长
范围、法术表和装备数值约束，因此提示级合成契约不足以关闭发布门。

下一步不继续扩写提示，改为评估服务器控制的类型化 Fact Ledger 与流式输出前校验；Feature Flag
继续关闭。再次运行以下命令仍需取得明确授权：

Stage 3.2 已完成第一版 Fact Ledger：从已注册证据解析职业法术表、奖励专长范围、进阶要求、专长
条目和装备数值；候选答案在流出前校验，失败时只允许一次修正，连续失败不输出候选或来源。上一轮
六个 MiMo 错误答案离线回放 6/6 被拒绝，正样本与流式隔离测试通过。当时的本地下一门为
`request_real_fact_ledger_ab`，详见 `fact-ledger.md`。

经明确授权完成真实 Fact Ledger 复测：自动门从同日合成契约 Planner 对照的 5/6 降至 3/6，
严格完成率仍为 0/6；3 题安全拒答，平均时延 111.51s → 153.75s，模型 token
290,132 → 437,424。PF1E 动态预算 6 轮回归保持 6/6。发布门失败，下一步进入 §8.7 通用
Fact Ledger 核心、PF1E Adapter 与结构化修补整改，不启用 Feature Flag，也不直接重跑外部模型。

```bash
npm run eval:complex:pf:stage2
npm run eval:complex:pf:planner
```

两条命令都会向配置模型发送合成问题和已读规则证据；原始报告只能保存在 `/private/tmp`，不得提交
仓库。
