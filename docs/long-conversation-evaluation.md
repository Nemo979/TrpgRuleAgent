# 长会话与 revision 评测

## 范围

V2.3 Stage 0 使用同一份 12/24/48 轮夹具验证两层契约：

- 确定性评测：状态保留、显式覆盖、助手内容不污染状态、Context 裁剪、指代增强和 revision mismatch；
- 真实答案评测：把合成历史作为预置上下文，只执行每个 case 的目标规则问题，验证事实、来源和工具预算。

评测不改变线上 Prompt、检索排序或模型调用路径。

## Case Schema

`rulepacks/pathfinder-1e/evals/long-conversation-cases.jsonl` 每行包含：

- `history.turnCount`：12、24 或 48；
- `fillerUser` / `fillerAssistant`：不改变状态的合成轮次；
- `events`：指定轮次的用户明确状态和助手干扰文本；
- `expectedState`：以点路径表示的期望字段；
- `overwritePaths`：必须以最新用户声明覆盖的字段；
- `forbiddenState`：不得从助手文本或旧值进入最终 State 的字段；
- Context 分配、revision mismatch、Query Rewrite probe 的预期；
- `turns`：沿用答案评测的目标问题、金来源和必需事实。

合成历史会在内存中展开。确定性报告和 turn metrics 不写入历史正文、规则正文或答案。
答案评测的本地诊断报告沿用通用评测格式，会包含目标问题、模型答案和来源元数据；该文件位于
被 Git 忽略的生成目录，仅用于本地排错，不得提交。提交的基线结论只保留聚合数字和布尔结果。

## 命令

确定性契约：

```bash
npm run eval:long-context:pf
```

真实 MiMo 答案基线会发送合成历史、目标问题和已读 PF1E 证据，必须先取得授权：

```bash
TRPG_TURN_METRICS_PATH=data/pathfinder-1e/generated/long-conversation-turn-metrics.jsonl \
  npm run eval:answer:pf:long
```

## 2026-08-09 基线

- 确定性契约：3/3 case 通过；状态保留与覆盖正确率 100%，未确认状态污染率 0%，revision 与
  Query Rewrite probe 正确率 100%；强制小窗口下裁剪率 100%，最新消息全部保留；
- MiMo 真实答案：3/3 轮通过，来源命中 3/3，无依据率 0%；
- 工具预算：总调用 7，单轮最多 3，全部在预算内；
- 正式 Context：12/24/48 轮分别记录 222/391/737 History token，三轮裁剪消息数均为 0；
- 结论：Stage 0 发布门满足；未出现可复现的真实长会话裁剪退化，不启动滚动摘要。

## 发布门

- 三个长度档的 State、覆盖、污染、revision 和 probe 契约全部通过；
- 最新消息在裁剪后保留；
- PF1E 长会话目标答案事实和来源全部通过；
- 无依据率为 0%；
- 不增加正式聊天链路的模型调用；
- 提交到仓库的基线结论只保留聚合数字、case ID 和布尔结果；本地诊断报告不得提交。
