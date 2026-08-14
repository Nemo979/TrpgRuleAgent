# Stage 3 发布门整改：通用 Fact Ledger 与 PF1E Adapter

Stage 3.2 已将高风险构筑事实从提示约束提升为服务器硬校验。2026-08-12 已完成 §8.7 第一阶段的
等价迁移：通用模型位于 `fact_ledger.py`，Adapter 协议和注册表位于
`fact_ledger_adapter.py`，原 PF1E 解析与校验位于 `pf1e_fact_adapter.py`，应用组合根位于
`fact_ledger_defaults.py`。离线 PF1E 回放工具仍由 `trpg_app.fact_ledger_evaluation` 提供。

2026-08-13 已完成 §8.7 步骤 5：PF1E Adapter Schema/版本升至 2，并根据真实失败样本扩充
Evidence 驱动的专长时间线、前提图、BAB 和法术元数据。通用核心保持规则系统无关。

同日完成 §8.7 步骤 6：通用 `AnswerDraft`、`RepairPatch`、受限合并和确定性 Markdown Renderer
已接入默认关闭的 Fact Ledger 路径。服务器最多请求一次只覆盖失败 claim 的 patch，完整重验证
后才发送答案；Schema、修补、渲染或复验失败均产生独立安全拒答事件且不附来源。

2026-08-11 真实复测后决定：后续不继续在聊天链路中直接扩写 PF1E 正则和校验器，而按
`v2.3-iteration-plan.md` §8.7 拆分为通用 Fact Ledger 核心与 PF1E Adapter。以下先记录现状，再
定义已落地边界和仍待完成的本地/真实模型发布门。

## 当前等价迁移边界

- Ledger 只解析本轮 `read_rules` 已注册的来源，不调用模型，也不使用内置 PF1E 常识补值；
- 当前支持法师职业法术表、通用/职业/种族专长槽、战士 BAB、专长前提图、法术条目元数据、
  法师奖励专长范围、进阶职业要求、专长表和带引用的装备数值；
- 无法解析的表格或字段保持未知，不能成为允许模型补全的空位；
- `enable_fact_ledger` 与 `enable_complex_planner` 分离且均默认关闭；只有 Planner 实际启用、当前规则库
  精确命中兼容 Adapter 且本轮有已读 Evidence 时才执行硬校验；
- turn metrics 记录核心/Adapter 版本、命中或降级状态、known/unknown/conflicting 数量、构建/验证
  耗时和问题数，不记录事实正文、用户问题、答案或失败句子。

## 已落地的第一阶段架构

```text
Complex Planner / Answer Draft
              │
              ▼
通用 Fact Ledger Core
├── FactRecord 与 known/unknown/conflicting
├── Evidence provenance
├── Adapter 注册与版本
├── ValidationIssue 生命周期
├── Adapter 失败时的安全降级
└── 版本、状态、数量与耗时指标
              │
              ▼
当前 LibraryManifest 对应 Adapter
└── PF1E Adapter：职业、专长、法术、进阶职业、装备
```

通用核心不得枚举或判断“法师”“战士”“专长”“法术环级”等 PF1E 概念。Adapter 由当前规则库的
稳定 `id + system + edition` 选择，只能读取该规则库、本轮已注册 Evidence。没有 Adapter 时跳过
事实硬校验并继续稳定回答路径，不能猜测规则系统或套用 PF1E Validator。

第一版不建设万能规则 DSL。通用接口只覆盖事实状态、来源和验证生命周期；具体 predicate/value
Schema 由 Adapter 拥有并版本化。通用 Core 不理解 claim 的规则语义；Adapter 通过精确
`ValidationIssue.path` 把失败映射到稳定 claim。草稿、patch、合并约束和 Renderer 由规则系统
无关的 `fact_ledger_repair.py` 提供。

## 当前 PF1E Adapter 硬校验

候选答案在对外发送前检查：

1. 职业等级分配总和是否等于声明的角色等级；
2. 是否给进阶职业增加了已读要求中不存在的条件；
3. 法师奖励专长是否被扩展到证据不允许的类别；
4. 法师最高法术环级和基础每日法术位是否匹配职业表；
5. 推荐的专长是否存在于本轮已读专长条目；
6. 引用标签是否属于本轮已注册来源；
7. 带引用的价格、伤害、AC 和百分比数值是否出现在相应来源。
8. 目标等级区间内的通用、职业和种族专长槽是否都安排了选择；
9. 专长选择时是否已满足前置专长、BAB 和证据明确的属性要求；
10. 法术的法师环级、学派、持续时间和豁免是否与已读法术条目一致；
11. 推荐的具名法术是否有本轮已注册法术条目支持。

最终生成器先返回严格 `AnswerDraft` JSON。服务器确定性渲染并完整校验；若存在全部可修补且能
精确映射到 claim 的错误，只允许同一模型返回一次 `RepairPatch`。Patch 必须精确覆盖全部失败
目标，不能新增目标或修改已验证 claim，引用只能来自本轮注册 Evidence。服务器合并后再次完整
渲染和验证；任一边界失败都不输出候选内容、不附规则来源，只返回安全失败。失败候选不会进入
会话历史。

## 当前实验流式行为

Planner 候选必须完整生成后才能验证，因此该实验路径会延迟首次文本；验证通过后以最多 24 字符的
SSE `text_delta` 继续增量显示。普通路径仍保持供应商流实时转发。该取舍只存在于默认关闭的 Planner
路径。结构化路径额外发送 `safe_refusal` 事件，用于把安全拒答与无依据答案分开统计；旧客户端可
忽略该事件。真实 A/B 必须同时观察首字延迟、总延迟、修补调用率和安全拒答率。

## 本地验收

2026-08-11 使用上一轮六个真实 MiMo 错误答案作为完全合成负样本回放：

- 6/6 被拒绝，未调用外部模型；
- 覆盖 `invented_prestige_requirement`、`bonus_feat_scope`、`unsupported_named_option`、
  `spell_slot_count`、`class_level_sum` 和 `unknown_citation`；
- 正样本单元测试确认一致的 12 级职业分配、合法超魔奖励专长和法术节点可以通过；
- 首份错误候选不会产生 `text_delta`，修正后的第二份候选才按 24 字符增量输出；连续两次错误时
  `sources` 为空。

负样本报告只保存在
`/private/tmp/stage3-fact-ledger-negative-regression-2026-08-11.json`。

迁移金线固定信息如下，不提交原始问题、答案或规则正文：

- 迁移前权威提交：`6704b9a`；
- 迁移前 PF1E Ledger blob：`15675a8da68ef325609dd9b03e9a88e9420783bc`；
- 六题历史输入 SHA-256：`8f3effbea96af6fee63efce604810705839cdf70d5a2ec5fcde6eee426097ac6`；
- 历史负样本聚合 SHA-256：`23be21e7c14675540ebc8b91977fc86ef91a08e815c8351de9dad79e46e9bea3`。

历史离线报告中部分来源没有 `metadata.structuralBlocks`，回放器无法恢复正文，可能把已注册来源记为
`unknown_citation`；因此该 6/6 聚合只作为历史观测冻结，不作为生产行为等价的唯一 oracle。迁移门改用
仓库内完整合成 Evidence，精确比较公开投影、记录数、有序 `(code, message)` 和正样本空问题集。

2026-08-13 等价迁移后的完整回归为 Python App 196 项、Retrieval 64 项、Vitest 268 项；两套
TypeScript 检查和两套 Web 生产构建全部通过。通用核心及其测试夹具不含 PF1E 规则概念；PF1E
三元组命中、GSS 未注册、协议不兼容、解析/发布/验证失败、无事实和跨库 Evidence 均有确定性测试。

同日步骤 5 新增完全合成金线并重放本地历史报告中的三个已知漏判形状，不提交原始问答或规则
正文：战士专长链缺少通用专长槽、人物创建少算通用专长槽、法术方案环级/学派错误均被拒绝；完整
时间线、满足前提的专长链和正确法术元数据均通过。三题 Adapter 公开投影约 1.3～2.5 KB，内部
完整专长表只用于确定性验证，不整体注入 Planner 提示。完整回归为 Python App 210 项、Retrieval
64 项、Vitest 268 项，两套 TypeScript 检查和两套 Web 生产构建均通过。本阶段未调用外部模型。

离线回放命令示例：

```bash
PYTHONPATH=services/app-python/src .venv312/bin/python \
  -m trpg_app.fact_ledger_evaluation \
  --input /private/tmp/planner-answer-report.json \
  --expect-rejected \
  --report /private/tmp/fact-ledger-negative-report.json
```

经明确授权，同日完成真实 MiMo 复测。规范实验组自动门为 3/6，严格完成率仍为 0/6；3 个错误
候选被安全拒答，但另外 3 个自动通过答案仍存在专长时间线、法术环级/学派或前提错误。相对同日
合成契约 Planner 对照组，平均端到端时延从 111.51s 增至 153.75s，模型总 token 从 290,132
增至 437,424；工具调用保持 52。已有 PF1E 6 轮动态预算回归仍为 6/6。

因此真实质量门仍未通过，`enable_complex_planner` 与 `enable_fact_ledger` 继续默认关闭。步骤 5
和步骤 6 已完成；步骤 7 本地门也以 Python App 221、Retrieval 64、Vitest 268、两套 TypeScript
检查及两套 Web 构建全部通过。确定性成本探针证明无修补为 1 次、触发修补最多 2 次模型调用，
报告可分别比较首次生成、验证、草稿解析、修补和渲染耗时。下一步须另行申请真实 MiMo A/B；
本阶段没有调用外部模型，也不因本地门通过自动获得外发授权。

2026-08-14 经授权完成同六题真实 MiMo A/B。Planner 对照自动门为 4/6、来源命中 6/6；结构化
Fact Ledger 实验为 0/6，并对 6/6 安全拒答：5 轮 `unrepairable_issue`、1 轮
`invalid_answer_draft`，没有一轮实际发起或应用 patch。工具调用均为 52；平均总延迟从 121.53s
升至 157.38s（+29.49%），平均模型 token 从 48,140 升至 50,424（+4.75%）。失败候选没有
流出，安全边界通过，但可用性、质量和成本门失败，两个 Feature Flag 继续默认关闭。

下一阶段回到本地修复服务器拥有的 claim path/schema 契约：生成器必须从 Adapter 发布的规范路径
中选择，指标增加稳定 issue code/path-match 分类，并用真实输出形状构建不含规则正文的回放夹具。
出现确定性“发现可修补问题 → 一次 patch → 完整复验通过”金线前，不再次请求外部 A/B。

同日完成上述本地整改。Adapter 协议 v2 新增服务器拥有的 `DraftPathSpec`，PF1E Adapter v3 发布
有限 canonical 模板与 alias；生成提示只公布该词表，严格解析时将 alias 规范化为 Validator 使用的
canonical path，并拒绝契约外路径。修补资格失败现稳定区分 `nonrepairable`、`missing_path` 和
`path_not_found`，观测只记录这些分类与 issue code。完全合成夹具已证明 alias 草稿可完成一次精确
patch、保留已验证 claim，并在完整重验证通过后才发布；Schema 偏差、混合可修补性、越权 patch 和
重验证失败仍安全拒答且来源为空。完整回归为 Python App 227、Retrieval 64、Vitest 268、两套
TypeScript 检查和两套 Web 构建全部通过。本轮未调用外部模型，两个 Flag 继续默认关闭；新的真实
MiMo A/B 仍需另行授权。
