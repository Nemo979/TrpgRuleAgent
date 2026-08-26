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

Planner 已改为只依赖通用 Ledger/Adapter 接口：根据当前 `LibraryManifest` 的
`id + system + edition` 精确选择 Adapter，把本轮已注册 Evidence 转换为 FactRecord。Planner
不导入 PF1E Parser 或 Validator；没有匹配 Adapter、协议不兼容或 Adapter 失败时记录低基数原因，
不注入 Ledger 提示、不安装 Validator，沿用原回答与流式路径。带稳定 claim ID 的 AnswerDraft 和
局部修补仍属于 §8.7 后续步骤。

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

通用 Fact Ledger 核心与 PF1E Adapter 等价迁移已于 2026-08-12 完成，新增独立且默认关闭的
`enable_fact_ledger`。下一步不继续扩写提示，而按失败样本扩充 PF1E 覆盖，再实现结构化局部修补；
两个 Feature Flag 继续关闭。再次运行以下命令仍需取得明确授权：

Adapter v3 路径契约整改完成后，经再次明确授权执行同六题真实 A/B：Planner 对照自动门 6/6、
严格 0/6；路径契约 Fact Ledger 自动与严格均为 0/6，4 题安全拒答、2 题只完成局部目标。实验组
尝试修补 1 次但因 `path_not_found` 未应用，平均延迟增加 10.82%，平均模型 token 增加 4.31%。
真实 patch 金线仍未闭合，两个 Feature Flag 继续默认关闭，下一步回到本地失败形状回放。

required-path 整改后再次授权的真实 A/B 中，Planner 自动 4/6、严格 0/6；Fact Ledger 自动
1/6、严格 0/6、4/6 安全拒答。真实输出首次完成 5 个局部 patch 应用，但完整复验失败而未发布；
另一次修补以 `path_ambiguous` 拒绝。平均延迟增加 75.01%，平均模型 token 增加 25.80%，发布门
继续失败。

PF1E Adapter v5 再次授权的真实 A/B 为 Planner 自动 5/6、严格 0/6；Fact Ledger 自动 0/6、
严格 0/6、5/6 安全拒答。新 Validator 成功发现紧凑法术位列错位，但 3 次实际修补调用均返回
无效 patch，平均延迟增加 71.76%、模型 token 增加 62.13%。下一步转为本地修补协议整改。

2026-08-15 纯本地整改已将 Adapter 协议升至 v4、PF1E Adapter 升至 v6。修补器现在接受
Adapter 明确发布且受 issue code 限制的唯一 sibling mapping；repair prompt 使用逐目标完整 JSON
operation 骨架，服务器固定除正文外的全部字段，并对 invalid patch 形状做受限分类。全仓本地门
通过，未调用外部模型；Planner 与 Fact Ledger Flag 继续默认关闭。

经 2026-08-18 明确授权的 v6 同六题真实 A/B 中，Planner 对照自动 5/6、严格 0/6；Fact Ledger
自动 0/6、严格 0/6、5/6 安全拒答。最小 patch 骨架首次在真实 MiMo 输出上完成合规 5-operation
应用，但完整复验仍失败；四次草稿结构拒答与显著成本增幅使发布门继续失败。两个 Flag 保持关闭，
下一阶段回到本地整改。

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

## 2026-08-20 Fact Ledger v7 本地整改

本轮仅调整默认关闭的 Fact Ledger 实验链路，没有改变 Planner 的启用条件。Adapter 协议 v5 与
PF1E Adapter v7 让服务器提供完整 required draft 骨架并拥有 `required_N` 到 canonical path 的
绑定；修补上下文按精确 issue 索引隔离，避免同一 `feat_timeline_slot_count` 在多个等级间串槽。
受限 `spell_slot_count` sibling mapping 已通过生成、一次 patch、完整复验与发布端到端回归。
本轮没有外部模型调用，下一次真实 MiMo A/B 仍需新的明确授权。

经 2026-08-20 明确授权完成 v7 同六题真实 A/B：Planner 对照自动 5/6、严格 0/6；Fact Ledger
实验组自动 0/6、严格 0/6、6/6 安全拒答。实验组 3 次应用合规 patch 但完整复验均失败，另有
`path_not_found`、patch `invalid_fields` 与 draft `invalid_evidence_ref`。工具调用均为 52，
实验组平均模型 token 增加 77.92%。发布门未通过，Planner 与 Fact Ledger 继续默认关闭。

同日完成 v8 纯本地协议整改：Validator 使用不发布的服务器 claim path marker 精确绑定专长槽与
`unsupported_named_option`；Repair Patch Schema v2 只接收有序 replacement 文本，固定目标字段
不再由模型复制。marker 注入边界及不含等级标签的专长修补完整金线均通过。本轮没有外部模型调用，
两个 Feature Flag 继续默认关闭。

经明确授权完成 v8 真实 A/B：Planner 对照自动 5/6、严格 0/6；实验组自动 0/6、严格 0/6、
5/6 安全拒答。Patch v2 两次均成功应用，`path_not_found` 与无效 patch 归零，但残余专长槽错误
仍阻止发布；三题在草稿结构边界拒答。实验组平均模型 token 增加 40.98%，两个 Feature Flag
继续默认关闭。

## 2026-08-22 Fact Ledger v9 本地整改

本轮未调用外部模型，也未改变 Planner 默认关闭状态。Adapter 协议 v7 / PF1E Adapter v9 将
required claim 的 Evidence 改为服务器分配，并将逐级专长正文收缩为“精确数量 + 有限选项集”的
类型化选择；模型返回数组后由服务器确定性渲染正文。全量本地门通过，下一次真实 MiMo A/B 仍需
单独授权。

经 2026-08-23 明确授权完成 v9 同六题 A/B：Planner 对照自动 5/6、来源 6/6、严格 0/6；
Fact Ledger 实验组自动 0/6、来源 0/6、严格 0/6，并对 6/6 安全拒答。六次失败均为草稿外层
`schema_fields`，未进入验证或修补。两个 Feature Flag 继续默认关闭，下一步只做最小 values
wire format 的本地整改。

2026-08-24 已完成 v10 纯本地整改，未调用外部模型。模型输出缩为单字段 values 数组，服务器拥有
完整 draft envelope、section、claim、path、确定性正文模板和 Evidence；新增稳定的 values 边界
失败分类与聊天流发布金线。全量本地门通过，Planner 与 Fact Ledger 继续默认关闭，真实 v10 A/B
仍需新的外发授权。

经新的明确授权完成 v10 同六题真实 A/B：Planner 对照自动 5/6、来源 6/6、严格 0/6；Fact
Ledger 实验组自动 0/6、来源 2/6、严格 0/6、4/6 安全拒答。实验组 5/6 通过最小 values 解析，
2/6 发布，三题进入验证或修补；一次修补应用 4 个 replacement 后完整复验失败。实验组平均
token 增加 43.68%，平均模型调用增加 33.33%；并行延迟仅作方向性参考。质量与成本门未通过，
Planner 和 Fact Ledger 继续默认关闭。

2026-08-24 已完成 v11 纯本地整改，未调用外部模型。Adapter 协议 v9 / PF1E Adapter v11 把法师
逐级法术进度改为服务器确定性正文，模型在相应 draft 槽只能返回 `null`；Repair 同样改为单字段
values wire，并对逐级专长复用 exact count 与有限 catalog。奖励专长范围问题可在缺失等级路径时
按唯一实际文本定位原 claim。全量本地门通过，Planner 与 Fact Ledger 继续默认关闭；真实 v11
A/B 仍需新的外发授权。

经 2026-08-25 明确授权完成 v11 同六题真实 A/B：Planner 对照自动 5/6、来源 6/6、严格 0/6；
Fact Ledger 实验组自动与来源均为 1/6、严格 0/6、5/6 安全拒答。唯一发布题的确定性法术进度
正确，但自由文本 claim 存在跨 topic 事实与证据错配。本次没有生成逐 turn metrics，不能比较
token、模型调用或延迟。Planner 与 Fact Ledger 继续默认关闭。

2026-08-25 已完成 v12 本地整改，未调用外部模型。服务器 claim 不再占用模型 wire 槽；剩余文本
值由服务器执行 topic 语义边界；Repair v3 兼容 MiMo 的精确单层 required_output 包装；评测入口
自动生成 metrics 与 observability 文件。Python App 262/262、Retrieval 64/64、Vitest 268/268、
全部类型检查、两套 Web 构建和 diff 检查通过。两个 Flag 保持默认关闭；真实 v12 A/B 需针对本轮
明确授权。

经针对 v12 的明确授权完成同六题 A/B：Planner 对照自动 6/6、来源 6/6、严格 0/6；Fact Ledger
自动 0/6、来源 1/6、严格 0/6、5/6 安全拒答。whole-string `semantic_scope` 造成两题误拒，另有
draft/Repair JSON 与专长槽复验失败；成本和延迟均明显上升，两个 Flag 继续关闭。

2026-08-25 已完成 v13 纯本地整改，未再次调用外部模型。topic 子句由服务器抽取；专长槽改成
普通/职业奖励/种族奖励有序独立 catalog；叙述式专长可进入有限域；证据不足不再退回自由文本；
奥法骑士施法推进由服务器确定；draft 与 Repair 各有一次 JSON-only 恢复。Python App 268/268、
Retrieval 64/64、Vitest 268/268、全部类型检查、两套构建与 diff 检查通过。真实 v13 A/B 需新授权。

经明确授权完成 v13 同六题真实 A/B：Planner 对照自动 5/6、来源 6/6、严格 0/6；Fact Ledger
实验自动与来源 1/6、严格 0/6、2/6 安全拒答，另有 3/6 模型超时。唯一发布题的服务器施法推进
正确，但条件分支不符合题意；两次拒答仍为 `semantic_scope`。两个 Flag 继续默认关闭，后续只先
做本地 v14。

v14 纯本地整改已完成，未再次外发。Fact Ledger 自由文本 claim 在没有合法 topic 子句时改为
服务器局部“证据不足、不臆测”fallback，并记录受限次数；结构化生成证据正文限制为 48,000 字符，
但保留全部来源标签/标题/路径，服务器 ledger 仍使用完整证据。Python App 270/270、Retrieval
64/64、Vitest 268/268、全部类型检查、两套构建与 diff 检查通过。两个 Flag 继续默认关闭，真实
v14 A/B 需新的明确外发授权。
