# Stage 3 发布门整改：通用 Fact Ledger 与 PF1E Adapter

Stage 3.2 已将高风险构筑事实从提示约束提升为服务器硬校验，但当前实现仍是 PF1E 原型。实现位于
`services/app-python/src/trpg_app/fact_ledger.py`，离线回放工具位于
`trpg_app.fact_ledger_evaluation`。

2026-08-11 真实复测后决定：后续不继续在聊天链路中直接扩写 PF1E 正则和校验器，而按
`v2.3-iteration-plan.md` §8.7 拆分为通用 Fact Ledger 核心与 PF1E Adapter。以下先记录现状，再
定义尚未实现的目标边界。

## 当前 PF1E 原型边界

- Ledger 只解析本轮 `read_rules` 已注册的来源，不调用模型，也不使用内置 PF1E 常识补值；
- 当前支持法师职业法术表、法师奖励专长范围、进阶职业要求、专长表和带引用的装备数值；
- 无法解析的表格或字段保持未知，不能成为允许模型补全的空位；
- Fact Ledger 只在默认关闭的 `enable_complex_planner` 路径启用，不影响简单问题和 Stage 2；
- turn metrics 只记录 Ledger 版本、记录数和校验问题数，不记录事实正文或失败句子。

## 目标架构（尚未实现）

```text
Complex Planner / Answer Draft
              │
              ▼
通用 Fact Ledger Core
├── FactRecord 与 known/unknown/conflicting
├── Evidence provenance
├── Adapter 注册与版本
├── ValidationIssue 生命周期
├── 结构化 Repair Patch
└── 安全输出与指标
              │
              ▼
当前 LibraryManifest 对应 Adapter
└── PF1E Adapter：职业、专长、法术、进阶职业、装备
```

通用核心不得枚举或判断“法师”“战士”“专长”“法术环级”等 PF1E 概念。Adapter 由当前规则库的
稳定 `id + system + edition` 选择，只能读取该规则库、本轮已注册 Evidence。没有 Adapter 时跳过
事实硬校验并继续稳定回答路径，不能猜测规则系统或套用 PF1E Validator。

第一版不建设万能规则 DSL。通用接口只覆盖已经得到失败样本证明的事实状态、来源、验证、局部修补
和安全输出能力；具体 predicate/value Schema 由 Adapter 拥有并版本化。

## 当前原型硬校验

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

## 当前原型流式行为

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

当前完整回归为 Python App 174 项及 23 个子测试、Retrieval 64 项、Vitest 268 项，TypeScript
检查和 Web 生产构建全部通过；Complex 结构集保持 10/10。

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

因此真实质量门未通过，`enable_complex_planner` 继续默认关闭。下一门为 §8.7 发布门整改：先完成
通用核心与 PF1E Adapter 的行为等价迁移，再补齐通用/职业专长时间线、专长前提图、法术环级和
学派元数据，把自由文本整答重试改为结构化修补或服务器渲染，并单独统计安全拒答；完成本地正负
样本前不再请求外部 A/B。
