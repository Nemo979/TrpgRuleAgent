# Stage 3 发布门整改：通用 Fact Ledger 与 PF1E Adapter

Stage 3.2 已将高风险构筑事实从提示约束提升为服务器硬校验。2026-08-12 已完成 §8.7 第一阶段的
等价迁移：通用模型位于 `fact_ledger.py`，Adapter 协议和注册表位于
`fact_ledger_adapter.py`，原 PF1E 解析与校验位于 `pf1e_fact_adapter.py`，应用组合根位于
`fact_ledger_defaults.py`。离线 PF1E 回放工具仍由 `trpg_app.fact_ledger_evaluation` 提供。

2026-08-11 真实复测后决定：后续不继续在聊天链路中直接扩写 PF1E 正则和校验器，而按
`v2.3-iteration-plan.md` §8.7 拆分为通用 Fact Ledger 核心与 PF1E Adapter。以下先记录现状，再
定义已落地边界和后续仍未实现的整改项。

## 当前等价迁移边界

- Ledger 只解析本轮 `read_rules` 已注册的来源，不调用模型，也不使用内置 PF1E 常识补值；
- 当前支持法师职业法术表、法师奖励专长范围、进阶职业要求、专长表和带引用的装备数值；
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
Schema 由 Adapter 拥有并版本化。结构化局部修补、确定性 Renderer 和 PF1E 新事实覆盖仍属于下一阶段。

## 当前 PF1E Adapter 硬校验

候选答案在对外发送前检查：

1. 职业等级分配总和是否等于声明的角色等级；
2. 是否给进阶职业增加了已读要求中不存在的条件；
3. 法师奖励专长是否被扩展到证据不允许的类别；
4. 法师最高法术环级和基础每日法术位是否匹配职业表；
5. 推荐的专长是否存在于本轮已读专长条目；
6. 引用标签是否属于本轮已注册来源；
7. 带引用的价格、伤害、AC 和百分比数值是否出现在相应来源。

首次候选失败时，服务器把结构化错误原因交给同一模型重试一次。第二次仍失败则不输出候选内容，
不附规则来源，只返回安全失败。失败候选不会进入会话历史。

## 当前实验流式行为

Planner 候选必须完整生成后才能验证，因此该实验路径会延迟首次文本；验证通过后以最多 24 字符的
SSE `text_delta` 继续增量显示。普通路径仍保持供应商流实时转发。该取舍只存在于默认关闭的 Planner
路径，真实 A/B 必须同时观察首字延迟、总延迟和二次生成率。

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

因此真实质量门仍未通过，`enable_complex_planner` 与 `enable_fact_ledger` 继续默认关闭。下一步是
§8.7 步骤 5：根据已有失败样本补齐通用/职业专长时间线、专长前提图、法术环级和学派元数据；
随后把自由文本整答重试改为结构化修补或服务器渲染，并单独统计安全拒答。完成新的本地正负样本
与成本门前不再请求外部 A/B。
