# Answer Evaluation

## 目的

答案级评测判断模型**最终自然语言答案**是否事实正确、引用了正确来源、在合理工具预算内完成，且没有凭空下结论。它与 `retrieval-evaluation.md` 的检索层评测（Hit@K / MRR）互补：检索评测只关心"正确章节是否进入候选前 K"，答案评测关心"最终回答是否成立"。

实现位于 `services/app-python/src/trpg_app/answer_evaluation.py`，复用正式链路的 `run_rule_turn`（与线上完全一致的鉴权、SSE 工具编排与收敛、引用注入），对一组 `AnswerCase` 跑多轮对话并逐题打分。

## 指标

| 指标 | 字段 | 含义 |
| --- | --- | --- |
| 事实覆盖 | `missingRequiredAny` / `passed` | 答案归一化后须包含每个 `requiredAny` 分组中至少一个候选表述；任一组缺失即判失败。 |
| 引用支持度 | `sourceMatch` | 模型最终附带的来源中，至少有一个 `documentId` 或 `legacyParentId` 落在题面 `relevantIds` 内。 |
| 工具预算 | `toolCalls` / `withinBudget` | 统计 SSE 中 `searching`/`reading` 状态事件数作为工具调用近似计数；超过软阈值 `TOOL_CALL_BUDGET`（默认 12）标记超预算。 |
| 无依据结论率 | `unsupportedTurns` / `unsupportedRate` | 已给出非空答案、但未命中相关来源、且无错误的轮次占比；衡量"答了却没依据"的比例。 |

聚合报告还包含 `passRate`（通过轮次占比）、`toolCallTotal` / `toolCallMax`。

归一化（`_normalize`）对全角/半角、空格、多种连字符做统一，避免"–2"与"-2"被判为不同事实。

## 题集格式

每行一个 JSON。`rulepacks/pathfinder-1e/evals/answer-cases.jsonl`（v2.0，5 个多轮 case）与 `v2.1-answer-cases.jsonl`（v2.1，4 轮对话）已是带真实 gold 来源 ID 与事实点的题集，可直接作为复现输入，也是格式参照：

```json
{"id":"example.flanking","turns":[
  {"query":"夹击对攻击检定有什么影响？",
   "relevantIds":["pf1e-d27574258bdd"],
   "requiredAny":[["+2","加2","夹击加值"]]}
]}
```

- `id`：题面标识。
- `turns`：多轮序列，逐轮追加到对话历史。
- `query`：用户问题。
- `relevantIds`：该题期望被引用的来源 ID（支持 `legacyParentId` 兼容，如 `pf1e-xxxx:entry:yyyy`）。
- `requiredAny`：每组为"任一命中即可"的事实表述候选列表；全部组命中才算事实正确。

## 复现

```bash
PYTHONPATH=services/app-python/src:services/retrieval-python/src \
  .venv312/bin/python -m trpg_app.answer_evaluation \
  --config config/app.yaml \
  --documents data/libraries/pathfinder-1e/current/documents.jsonl \
  --index-dir data/libraries/pathfinder-1e/current/vector-index \
  --cases rulepacks/pathfinder-1e/evals/answer-cases.jsonl \
  --models mimo-v2.5 agnes sensenova \
  --report data/pathfinder-1e/generated/answer-eval.json
```

或通过 npm：

```bash
npm run eval:answer:pf
```

### 运行基线的环境前提

真实模型基线需要以下三者齐备，**本仓库与当前开发环境默认不具备**，运行前须先准备：

1. **模型配置** `config/app.yaml`：从 `config/app.example.yaml` 复制并填入三模型的 API base / key / 模型名（密钥不入库，仅本地/环境变量）。
2. **已建索引的规则库**：`data/libraries/pathfinder-1e/current/` 下的 `documents.jsonl` 与 `vector-index/`（由 `rules:import:pf` + `vector:build:pf` 生成；`data/` 全 gitignore，由使用者自备合法 PDF/CHM 导入）。
3. **可访问的模型 API**：`--models` 列出的模型须在上述配置中可达。

缺任一条件时脚本会因连接/索引缺失而失败；离线环境只能验证题集解析与评分逻辑（见下方单测）。

## 当前局限与待补

- `requiredAny` 是关键词/表述命中，不是语义事实判定；强事实正确性需接入 LLM-judge 或人工复核。
- 引用支持度对照的是**金标** `relevantIds`，若金标不全，可能误判合法引用为不支持；这是已知偏差。
- 工具预算为 SSE 状态事件的近似计数，不区分搜索与读取，也不等于服务端 `EvidenceBudget` 内部计数。
- PF1E 已有 v2.0 / v2.1 两套真实金标题集；**Agnes / SenseNova 与 GSS 的真实金标题集尚未建立**，且尚无 LLM-judge 事实信号。
