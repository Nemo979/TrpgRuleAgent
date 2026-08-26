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

经再次明确授权，同日完成 claim path/Schema 整改后的同六题真实 A/B。Planner 对照自动门为
6/6、来源 6/6，但严格完成率仍为 0/6；路径契约 Fact Ledger 实验为 0/6，4 题安全拒答、2 题
只发布局部短答案，严格完成率同为 0/6。实验组 3 轮 `invalid_answer_draft`，1 轮
`unrepairable_issue` 稳定细分为 `path_not_found`；检测到 `feat_timeline_slot_count` 与
`unsupported_named_option` 后尝试修补 1 次，但未应用 patch。工具调用保持 52，平均总延迟
112.47s → 124.64s（+10.82%），平均模型 token 48,302 → 50,385（+4.31%）。

真实输出证明 canonical 词表与稳定分类已经生效，但仍未闭合 path-to-claim 映射，也未产生一次
真实 patch 成功路径。两个 Feature Flag 继续默认关闭；下一阶段仅在本地固定本轮失败形状、补充
局部完成度回放并恢复确定性金线，新的全仓门通过前不再次运行外部 A/B。

同日本地后续整改将 Adapter 协议升至 v3、PF1E Adapter 升至 v4。Adapter 除 canonical/alias
模板外，还按本轮服务端目标发布必须覆盖的精确路径；草稿缺少任一路径时在渲染前拒绝，因此局部
短答案不再能通过结构化边界。提示中的伪 Schema 已替换为真实合法 JSON 示例。对于 Validator 路径
与生成 claim 路径不完全相同的情况，服务器只允许两类确定性映射：唯一的结构父子路径，或
`unsupported_named_option` 的唯一具名文本命中；任何多候选均分类为 `path_ambiguous` 并拒绝。
草稿解析失败只记录 `invalid_json`、`schema_fields`、`missing_required_path` 等受限枚举，不记录
草稿正文。本地全量门为 Python App 230、Retrieval 64、Vitest 268，两套 TypeScript 检查及两套
Web 生产构建全部通过；本轮未调用外部模型，两个 Flag 仍默认关闭。

经新的明确授权（包括固定六题及其本轮 PF1E 检索证据外发范围），同日完成 required-path 整改后的
真实 MiMo A/B。Planner 对照自动门为 4/6、来源 6/6、严格 0/6；实验组自动门为 1/6、来源
2/6、严格 0/6，并对 4/6 安全拒答。实验组 2 次进入修补：一轮应用 5 个局部操作后完整复验仍
失败，另一轮因 `path_ambiguous` 拒绝；首次走到真实 patch 应用，但仍没有通过复验并发布的金线。
草稿失败稳定分类为 `missing_required_path` 与 `duplicate_identifier`。平均总延迟 96.21s →
168.38s（+75.01%），平均模型 token 47,888 → 60,245（+25.80%），工具调用均为 52。

required paths 已阻止局部短答案直接发布并改善诊断，但质量、误拒与成本门继续失败。两个 Flag
保持默认关闭；下一步回到本地补充职业表列对齐/等级范围 Validator、残余复验 issue 分类及更强的
结构化生成约束，不进入 Summary 或并行。

同日完成下一轮纯本地整改，未调用外部模型。PF1E Adapter 升至 v5：Validator 现在识别
`1环4位`、`4个1环` 和带显式 `0/1/2/3环` 标签的法术位向量，能够拒绝真实 A/B 中把 0 环列
错当成 1 环的紧凑等级表；存在职业表证据的目标等级还会成为服务器必需 `levels[n].spells` 路径。
重复 section/claim id 只属于内部 patch 定位信息，服务器现按出现顺序确定性消歧，并在提示中为
每条必需路径发布唯一建议 id，不再因重复 id 丢弃整份内容。patch 完整复验失败时额外聚合残余
issue code 与不含参数值的 canonical path template，不记录答案或路径参数。

脱敏合成回放覆盖本轮 `duplicate_identifier`、`missing_required_path`、紧凑法术表列错位和 patch
后残余问题形状。完整回归为 Python App 233、Retrieval 64、Vitest 268，两套 TypeScript 检查及
两套 Web 生产构建全部通过。两个 Flag 继续默认关闭；再次真实 A/B 仍需新的明确外发授权。

经再次明确授权，同日完成 PF1E Adapter v5 的同六题真实 MiMo A/B。Planner 对照自动门为
5/6、来源 6/6、严格 0/6；v5 实验组自动门为 0/6、来源 1/6、严格 0/6，并对 5/6 安全
拒答。v5 在真实输出上首次检出紧凑法术表的 `spell_slot_count`，但其 `spell_slots[m]` issue
无法映射到同级 `spells` claim，以 `path_not_found` 拒绝。另有 3 次实际修补调用全部返回不合
Schema 的 patch，零次应用。平均延迟增加 71.76%，平均模型 token 增加 62.13%。

服务器 ID 消歧本身生效；本轮 `duplicate_identifier` 分类实际来自重复 semantic path，需改名为
`duplicate_path`。发布门继续失败，下一阶段转向本地修补协议收缩、受限 sibling path 映射与
invalid patch 结构分类，不再通过增加 Validator 后立即重复外部 A/B。

2026-08-15 已完成上述纯本地修补协议整改，未调用外部模型。Adapter 协议升至 v4、PF1E Adapter
升至 v6：Adapter 可发布带 issue code 白名单的确定性 repair path mapping；PF1E 当前只允许
`spell_slot_count` 从 `answer.levels[n].spell_slots[m]` 映射到同级唯一的
`answer.levels[n].spells` claim。重复 semantic path 现独立分类为 `duplicate_path`。

修补提示不再发送完整 draft 或伪 Schema，而是按每个目标给出完整 `required_output` operation
骨架；模型只允许替换 `replacement_text`，服务器会拒绝未替换占位符、目标/issue/evidence 变化、
缺失或额外字段。失败观测新增 `invalid_json`、`invalid_fields`、`invalid_shape`、
`target_mismatch` 等受限分类，不记录 patch 正文。完整本地门为 Python App 237/237、Retrieval
64/64、Vitest 268/268，全部 TypeScript 检查和两套 Web 生产构建通过。两个 Flag 继续默认关闭；
下一次真实 A/B 仍需新的明确授权。

经 2026-08-18 新的明确授权，完成 v6 同六题真实 MiMo A/B。首轮 Planner 对照第六题发生远端
chunked 流中断，完整同配置重试后正式对照为自动 5/6、来源 6/6、严格 0/6；v6 实验组自动
0/6、来源 1/6、严格 0/6、5/6 安全拒答。实验组唯一一次修补返回合规的 5 个 operation，全部
成功应用，`factRepairPatchFailureReason` 均为 `none`，证明最小 patch 骨架修复了 v5 的真实
Schema 遵循问题；但完整复验仍残留 `feat_timeline_slot_count`，零次发布。

另外四题草稿分别分类为 `invalid_json`、`invalid_path`、`duplicate_path` 和
`missing_required_path`；本轮没有 `spell_slot_count`，因此 sibling mapping 未形成真实金线。
实验组平均延迟增加 77.59%、p95 增加 100.08%，平均模型 token 增加 25.79%。质量、误拒与成本
门继续失败，两个 Flag 保持默认关闭；下一阶段回到本地降低草稿结构失败与逐槽修补残余，不立即
再次运行外部 A/B。

2026-08-20 已完成 v6 失败形状对应的纯本地整改，未调用外部模型。Adapter 协议升至 v5、PF1E
Adapter 升至 v7。服务器现在向生成器发布完整 `required_output` 草稿骨架，并按 `required_N` id
强制绑定每条必需 claim 的 canonical path；生成器仍必须提供真实正文与已注册 Evidence，缺失
claim、未知证据、残留占位符或不合 Schema 的输出继续安全拒绝。解析器只额外容忍一份由说明文字
包裹的唯一顶层 JSON object，多对象或含竞争容器的歧义输出仍拒绝。

修补目标现保存其精确 issue 索引，同一 issue code 的不同等级/槽位不会再把彼此的 expected、actual
和消息混入同一目标。PF1E 的受限 `spell_slots[m]` → 同级 `spells` sibling mapping 已通过完整的
“生成 → 映射 → 一次 patch → 完整复验 → 发布”合成端到端金线。全量门为 Python App 242/242、
Retrieval 64/64、Vitest 268/268，两套 TypeScript 检查、两套 Web 生产构建和
`git diff --check` 全部通过。两个 Feature Flag 继续默认关闭；下一次同六题真实 MiMo A/B 需要
新的明确外发授权。

经新的明确授权，2026-08-20 完成 v7 同六题真实 MiMo A/B。Planner 对照自动门 5/6、来源
6/6、严格 0/6；v7 实验组自动 0/6、来源 0/6、严格 0/6，并对 6/6 安全拒答。实验组 5/6
进入修补，3 题分别应用 10、5、3 个合规操作，但完整复验均仍失败；其余失败为一次
`path_not_found`、一次 patch `invalid_fields` 和一次草稿 `invalid_evidence_ref`。草稿侧不再出现
`invalid_path`、`duplicate_path` 或 `missing_required_path`，证明服务器骨架与 path 强绑定生效，
但尚未形成真实发布金线。

两组工具调用均为 52；实验组平均模型 token 增加 77.92%，平均模型调用增加 66.67%。按用户要求
两组曾短暂并行，平均延迟增加 88.33%、p95 增加 101.64% 仅作为方向性成本信号。质量、误拒和
成本门继续失败，两个 Feature Flag 保持默认关闭；下一步回到本地修复专长槽替换语义、具名选项
到原 claim 的确定性映射，以及固定 patch 字段保护，不自动再次运行外部 A/B。

同日完成上述纯本地整改，未调用外部模型。Adapter 协议升至 v6、PF1E Adapter 升至 v8，Repair
Patch Schema 升至 v2。服务器在仅供 Validator 使用的文本中加入不可发布的 claim path marker，
因此逐级专长 claim 不再依赖模型在正文重复等级标签；`unsupported_named_option` 也能直接返回原
claim path，不再全文猜测。草稿和 replacement 若尝试注入保留 marker 会在结构边界拒绝。

Patch v2 只接受与服务器目标顺序一致的 replacement 字符串数组；claim id、path、evidence refs
和 issue codes 全部由服务器重建，模型不能再修改固定字段。真实 PF1E 合成金线已证明不含等级
文字的专长 replacement 仍能锚定正确槽位，并在完整复验通过后发布。全量门为 Python App
246/246、Retrieval 64/64、Vitest 268/268、全部 TypeScript 检查、两套 Web 生产构建及
`git diff --check` 通过。两个 Feature Flag 继续默认关闭；新的真实 A/B 仍需另行授权。

经新的明确授权，同日完成 v8 同六题真实 MiMo A/B。Planner 对照自动 5/6、来源 6/6、严格
0/6；v8 实验组自动 0/6、来源 1/6、严格 0/6、5/6 安全拒答。两次 Patch Schema v2 修补均
成功应用（6、3 个服务器绑定 replacement），没有 `path_not_found`、无效 patch 或固定字段被
模型修改；第六题还消除了原 claim 上的 `unsupported_named_option`，证明精确映射真实生效。

两次修补仍因残余 `feat_timeline_slot_count` 未通过完整复验，第五题还残留精确定位到
`answer.equipment.stats` 的非法选项；另有两次 `invalid_evidence_ref` 和一次 `schema_fields` 草稿
失败。工具调用保持 52，实验组平均模型 token 增加 40.98%、平均调用增加 33.33%。两组并行执行，
延迟只作方向性参考。质量、误拒与成本门继续失败；下一阶段应把逐槽专长选择和 Evidence 分配从
自由文本进一步收缩为 Adapter 发布的类型化服务器契约，而不是继续增加自然语言提示。

2026-08-22 已完成该 v9 纯本地整改，未调用外部模型。Adapter 协议升至 v7、PF1E Adapter 升至
v9。每个 required claim 现在都由 Adapter 发布服务器 Evidence；模型填写的 `evidence_refs` 不再
决定 required claim 的来源，也不能用伪造标签覆盖服务器绑定。逐级专长 claim 进一步发布精确
选择数、已读专长有限域与确定性正文模板，模型只返回选择数组，服务器验证数量、唯一性和成员资格
后重建正文与 Evidence。相同选项集合在 prompt 中使用共享 catalog，避免按等级重复膨胀。

新增回归覆盖越界/重复/数量错误选择、伪造 Evidence、自由文本服务器 Evidence、契约覆盖偏差和
未注册来源，均在发布边界拒绝。类型化 PF1E 草稿还通过了完整确定性生成、验证与发布金线。全量门为
Python App 251/251、Retrieval 64/64、Vitest 268/268、
两套 TypeScript 检查、两套 Web 生产构建及 `git diff --check` 通过。两个 Feature Flag 继续默认
关闭；下一次真实 MiMo A/B 仍需新的明确外发授权。

经 2026-08-23 新的明确外发授权，完成 v9 同六题真实 MiMo A/B。Planner 对照自动 5/6、来源
6/6、严格 0/6；v9 实验组自动 0/6、来源 0/6、严格 0/6、6/6 安全拒答。Adapter v9 在六题
均成功匹配，但所有实验草稿都以受限 `schema_fields` 分类在外层 Schema 边界拒绝，没有进入
Validator 或 Repair，因此真实运行尚未覆盖类型化专长发布金线。

两组工具调用均为 52、平均模型调用均为 1；实验组平均模型 token 增加 4.36%，平均延迟增加
22.83%，p95 增加 76.10%（并行运行，延迟仅作方向性参考）。质量、误拒和延迟门失败，两个
Feature Flag 保持默认关闭。下一阶段回到本地，把模型输出进一步缩成最小 values object；完整
envelope、section、claim、path、正文与 Evidence 由服务器重建，不自动再次运行外部 A/B。

2026-08-24 已完成该 v10 纯本地整改，未调用外部模型。Adapter 协议升至 v8、PF1E Adapter
升至 v10。required draft 的模型 wire output 现在只有 `{"values":[...]}`：数组长度与顺序由
required paths 固定，自由文本项只允许字符串，类型化专长项只允许满足服务器 exact count 和
catalog 的数组。section、标题、claim ID、canonical path、专长正文模板和 Evidence 均由服务器
构造；模型正文中的受限 S 标签会被移除，再由服务器附加真实引用。

外层字段、values 形状、数量和有限域错误分别稳定分类为 `values_fields`、`values_shape`、
`values_count` 和 `invalid_selection`。新增聊天流端到端金线证明最小 values 经服务器重建、完整
验证后发布，伪造引用不会进入答案。全量门为 Python App 254/254、Retrieval 64/64、Vitest
268/268、两套 TypeScript 检查、两套 Web 生产构建及 `git diff --check` 通过。两个 Feature Flag
继续默认关闭；新的真实 MiMo A/B 仍需另行授权。

经新的明确外发授权，同日完成 v10 同六题真实 MiMo A/B。Planner 对照自动 5/6、来源 6/6、
严格 0/6；v10 实验组自动 0/6、来源 2/6、严格 0/6、4/6 安全拒答。最小 values wire 使
5/6 草稿通过解析，2/6 直接发布，另有 3/6 进入验证或修补；一次修补成功应用 4 个服务器绑定
replacement 后因残余专长槽问题未通过完整复验。相较 v9 六题全部在 outer Schema 拒绝，协议
收缩目标已得到真实验证。

两组工具调用均为 52；实验组平均模型调用增加 33.33%、平均 token 增加 43.68%、平均延迟增加
91.93%，p95 增加 48.08%（并行执行，延迟仅作方向性参考）。两份已发布答案仍分别存在法术环级
偏移和未完成具体配装的问题，严格均不通过。质量、误拒、延迟与成本门继续失败，两个 Feature
Flag 保持默认关闭；下一阶段先在本地类型化确定性法术进度并收缩 Repair wire，不自动再次外发。

2026-08-24 已完成 v11 纯本地整改，未调用外部模型。Adapter 协议升至 v9、PF1E Adapter 升至
v11、Repair Patch 内部 Schema 升至 v3。法师逐级最高环级和基础每日法术位现在由 Adapter 从已读
职业表确定性渲染；对应 draft value 只能保持 `null`，模型不能再生成或修改这些数值。Repair wire
也收缩为唯一合法外形 `{"values":[...]}`：自由文本项、有限域专长选择和服务器值分别由契约约束，
claim/path/issue/Evidence 与正文模板均由服务器恢复。

奖励专长范围错误在其等级 claim 不存在时，可按 issue 的实际选项文本唯一定位原 claim；多匹配仍
拒绝，不做猜测。类型化专长 Repair 会重新检查精确数量、唯一性、catalog 成员资格，并只允许契约
内 Evidence。新增回归覆盖 server-null 防篡改、确定性法术进度完整验证、类型化专长修补和缺失等级
路径定位及聊天流完整修补金线。全量门为 Python App 259/259、Retrieval 64/64、Vitest 268/268、两套 TypeScript
检查、两套 Web 生产构建及 `git diff --check` 通过。两个 Feature Flag 继续默认关闭；真实 v11
MiMo A/B 需要新的明确外发授权。

经 2026-08-25 新的明确外发授权，完成 v11 同六题真实 MiMo A/B。Planner 对照自动 5/6、来源
6/6、严格 0/6；v11 实验组自动 1/6、来源 1/6、严格 0/6、5/6 安全拒答。唯一发布的护甲施法题
中，服务器生成的法师 5–9 级最高环级与法术位全部正确，证明 server-owned 法术进度真实生效；
但自由文本 claim 跨 topic 重复法师护甲数值，逐 claim Evidence 语义不匹配，严格仍失败。

实验组两题 `invalid_answer_draft`、一题 `empty`、一题 `repair_validation_failed`、一题
`invalid_repair_patch`。本次启动未设置 `TRPG_TURN_METRICS_PATH`，没有逐 turn token、延迟、调用
次数或失败子分类，状态记录不推测这些数据。质量与误拒门继续失败，两个 Feature Flag 保持默认
关闭；下一阶段先在本地把服务器 claim 从模型 values 中完全移除，并类型化剩余自由文本语义。

2026-08-25 已完成 v12 纯本地整改，未调用外部模型。Adapter 协议 v10 / PF1E Adapter v12 将
server-owned claim 从 draft 和 Repair wire 中完全移除，由服务器按 canonical 顺序重建。剩余
build、conditional、equipment 文本新增 topic 说明和服务器执行的 required/forbidden term 语义
约束，跨 topic 内容稳定分类为 `semantic_scope`；角色声明属性与规则先决条件分开解析。

Repair v3 仅兼容精确单层 `required_output.values` 包装，任意其他外层字段仍拒绝。评测入口会从
`--report` 自动生成新 metrics JSONL 和 observability JSON。Python App 262/262、Retrieval
64/64、Vitest 268/268、两套 TypeScript、两套 Web 构建与 `git diff --check` 全部通过；两个
Flag 继续默认关闭。真实 v12 同六题 A/B 仍需针对本轮的明确外发授权。

经针对 v12 的明确授权完成真实 A/B：Planner 对照自动 6/6、来源 6/6、严格 0/6；Fact Ledger
自动 0/6、来源 1/6、严格 0/6、5/6 安全拒答。两题 `semantic_scope`、一次 draft JSON、一次
修补后残余专长槽和一次 Repair JSON 失败。实验平均 token +40.44%、模型调用 +33.33%、平均延迟
+45.38%、p95 +95.08%，所有发布门失败。

随后已完成 v13 纯本地整改，未再次外发。Adapter 协议 v11 / PF1E Adapter v13 采用 topic 子句
抽取代替 whole-string 语义拒绝；叙述式专长进入有限域；普通、职业奖励和种族奖励槽使用有序独立
catalog；证据不足的槽成为 server-owned 状态，不能退回自由文本。奥法骑士 1 级不推进、从 2 级
开始推进施法由服务器发布。draft/Repair 各有一次 JSON-only 恢复及受限指标。

Python App 268/268、Retrieval 64/64、Vitest 268/268、两套 TypeScript、两套 Web 构建和 diff
检查通过。两个 Flag 继续默认关闭；真实 v13 A/B 需要新的明确外发授权。

经明确授权完成 v13 真实 A/B：对照自动 5/6、来源 6/6、严格 0/6；实验自动与来源 1/6、严格
0/6、2/6 安全拒答、3/6 模型超时。唯一发布题验证了服务器奥法骑士施法推进，但未遵守用户指定
的 else 分支。两次拒答仍是 `semantic_scope`，JSON-only 恢复与 Repair 均未触发。有序选择组在
一份真实草稿中形成 3 个 selection claims / values，但未通过语义边界进入发布。两个 Flag 继续
默认关闭，下一阶段为纯本地 v14。

v14 纯本地整改将 Adapter 协议升至 v12、PF1E Adapter 升至 v14。bounded free-text contract 可
发布服务器 `semantic_fallback_text`：只有模型没有任何合法 topic 子句时才替换单个 claim，正文
明确说明证据不足且不臆测；fallback 不能用于 server-owned 或 selection contract。应用次数以
`factDraft.semanticFallbackCount` 聚合，不记录模型值或 fallback 正文。

结构化生成器看到的证据正文总量限制为 48,000 字符，并为每个注册来源保留标签、标题和路径；
服务器仍以完整证据构建和验证 ledger。全量本地门通过，两个 Flag 继续默认关闭；尚未运行真实
v14 A/B，新的外发需要再次授权。
