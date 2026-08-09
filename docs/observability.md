# V2.2 可观测性指标与基线报告

## 范围

Stage 1B 只记录每轮聊天的聚合数字，不改变上下文裁剪、工具预算、检索排序或模型调用次数。
实现位于 `services/app-python/src/trpg_app/observability.py`，报告聚合器位于
`services/app-python/src/trpg_app/observability_report.py`。

## 隐私边界

指标行只包含：

- request ID、model/library、意图、停止原因；
- 搜索查询的 SHA-256 短摘要，不包含查询原文；
- Context、Token、Evidence、工具次数和各阶段耗时数字。

不得写入问题、答案、规则正文、对话摘要正文或密钥。`TRPG_TURN_METRICS_PATH` 由服务管理员设置；
未设置时仍输出安全的 `turn_metrics` 日志，但不额外写 JSONL 文件。指标文件写入失败只告警，不能中断回答。

## Token 口径

- provider 返回 prompt/completion usage 时使用真实值；
- provider 未返回 usage 时，对该次调用分别估算消息 JSON 和可见输出；
- `reportedCalls` / `estimatedCalls` 与 `reportedRate` 明示真实值覆盖率；
- System、State、裁剪前/后 History、Evidence、输出预留和最终答案分别记录，估算值不用于改变线上预算。

## 采集与报告

运行答案评测时显式设置指标路径：

```bash
TRPG_TURN_METRICS_PATH=data/pathfinder-1e/generated/turn-metrics.jsonl \
  npm run eval:answer:pf
```

生成基线报告：

```bash
npm run report:observability:pf
```

报告提供每项指标的样本数、mean、p50、p95、max，以及 model/library/intent/stop reason 分布。
与已有报告比较时直接调用聚合器：

```bash
PYTHONPATH=services/app-python/src .venv312/bin/python \
  -m trpg_app.observability_report \
  --input data/pathfinder-1e/generated/turn-metrics.jsonl \
  --baseline data/pathfinder-1e/generated/observability-baseline.json \
  --report data/pathfinder-1e/generated/observability-current.json
```

`comparison.metricDeltas` 为 current - baseline；延迟、Token、Evidence 和工具次数均以负值代表下降。
真实模型基线会把评测问题与已读规则证据发送给所选模型服务，必须在数据来源和外部服务均获授权后执行。

## 当前 PF1E 基线

2026-08-09 经明确授权完成 MiMo 6 轮基线，usage 覆盖率 100%。总延迟 mean/p95 为
57.35/75.24 秒，累计 prompt token mean/p95 为 78,332/147,689；完整数据和决策见
[V2.2 Stage 1 数据评审](v2.2-stage1-review.md)。

## ContextBudget 发布门

Stage 3 第一阶段在相同 PF1E 构建和 MiMo 6 轮题集上达到 6/6，provider usage 覆盖率 100%。
累计 prompt token mean/p95 为 11,528/17,571，较 Stage 1 基线分别下降 66,804/130,118；
总延迟 mean 为 57.26 秒，基本持平。新增指标记录同轮工具内容压缩前后峰值、压缩消息数以及
History、Evidence、Output Reserve 的分配值。完整结论见
[V2.2 ContextBudget 数据评审](v2.2-context-budget-review.md)。
