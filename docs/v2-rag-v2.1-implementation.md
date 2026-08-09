# PF1e RAG V2.1 实施记录

## 当前范围

本轮完成了可复现的代码和测试改造：通用 PF1e 条目级切分、命中子块元数据、精确标题排序、多职业/Favored Class 语义区分、逐文档证据预算降级、四类问题意图、构筑检索计划和用户消息驱动的结构化角色状态。

## 固定失败样例

评测用例位于：

- `rulepacks/pathfinder-1e/evals/v2.1-retrieval.jsonl`
- `rulepacks/pathfinder-1e/evals/v2.1-answer-cases.jsonl`

用例覆盖油腻术正文、职业法术列表干扰、兼职/多职业与天赋职业区分，以及法师 5 级的四轮构筑追问。稳定条目 ID 必须在正式导入报告中映射，演示 fixture 不可作为正式验收数据。

## 数据源与候选修订状态

原始 PF1e CHM 已登记到受控本地源目录，SHA-256 为
`00d5689b1b99d7aa5192b6c81e84034dc89f47e4fd49a47bb45e10724ac3ab9d`。
候选修订为 `20260805T084935Z`，包含 4,539 个条目/表格文档和 42,858 个向量子块；候选质量门通过，已切换为 release 的 `current`。

质量门报告和候选索引审计位于候选修订的 `audit/` 目录。长期集 ID 审计显示现有 85/30 题评测引用的相关 ID 在候选、旧 current 和演示 fixture 中均不完整存在，不能把其当前 miss 率当作真实回退率；必须先从可审计的规则导出重新映射相关 ID。

## 验证记录

- Python 检索服务：40 项通过（含条目切分、子块字段、排序和既有检索测试）。
- Python app 服务：75 项通过。
- TypeScript 类型检查：通过。
- Web 生产构建：通过。
- Vitest：排除本环境启动无输出的 `apps/gateway/test/gateway.test.ts` 后，26 个测试文件、242 项通过。
- V2.1 专项检索：7/7 Hit@5，MRR 1.0；油腻术、多职业和天赋职业约束均通过。
- 条目质量门：重复条目 0、导航空壳 0、超证据预算 0。

## V2.2 Stage 0 修复：锚点章节切分与候选重建

V2.2 基线收拢时发现 8/5 结构化候选（`20260805T084935Z`）丢失 CRB 战斗规则整章：
Word 导出的 CHM 页面用 `<A name="章节名">` 锚点标记章节标题，audit 的 heading 信号与
`StructuredHtmlParser` 均未识别，页面被判为 `split_tables_with_context` 并整页压成
单文档。修复（`d232618`）：

- `chm_structure_audit`：将合理的 `<A name>` 标签计为 heading 信号（过滤 Word 工具
  锚点 OLE_LINK/URL 编码/数字标签），使章节页改判 `split_headings_and_tables`。
- `chm_structured`：解析器把内部文本匹配锚点名的 name 锚点升级为 level-2 heading，
  `partition_sections` 因此按锚点切出 10 个战斗子章节文档。
- 超预算目录条目（>80k 字符）按稳定的「等级：」字段段兜底拆分，段前段落为法术标题，
  解决无括号英文名的法术页把整页尾部聚合成单个超大 entry 的问题。
- 重复 entry 分组要求真实 `entryType`，纯导航壳父文档不再被误判为重复而拉低质量门。

重建候选改用 v1 数据源 CHM（v2.20 SC 提取，保留 2140 个父文档含属性/战斗规则等基础
章节；v1.9 easyread CHM 不含这些页面）。重建后 6533 个结构化文档、`overEvidenceBudget=0`、
`duplicateEntryContentGroupCount=0`、质量门通过；85/30 题相关 ID 100% 覆盖（旧 ID 直接
保留或经 `legacyParentId` 匹配）。

## V2.2 Stage 0 修复：table 策略页正文丢失与 overview 兜底

重建后评测发现 `split_tables_with_context` 页面把未归属条目的所有段落全部丢弃，
119 个父文档丢失 >50% 正文（如 CRB 状态 5895→186 字符、负载能力 5999→仅表格），
导致战斗/状态类题目漏召回。修复（`85ae374`、`a7b5f4a`）：

- 未被条目候选覆盖的段落合并为 overview 章节文档（`entryType=section`），正文重新可检索；
- overview 按段落分块（单段 >8k 字符时字符级切分），超长段落不再撑爆证据预算；
- 只重复章节标题的导航标签段落视为导航壳剔除，质量门恢复绿色。

最终候选：**7140 个结构化文档、质量门通过**，部署为 revision `20260809T004148Z`
（`data/libraries/pathfinder-1e/builds/`，旧 `20260805T084935Z` 保留为回滚点）。

## V2.2 Stage 0/1 基线评测结果（新候选）

- **85 题长期混合检索**：Hit@5 = **90.6%**、MRR = **0.7351**、约束全通过
  （修复前旧候选 Hit@5=78.8%，主要收益来自战斗/状态章节正文恢复）。
- **30 题结构化专项**：Hit@5 = **83.3%**、MRR = **0.6750**。5 个 miss 均为相关文档
  存在但排名未进 Top-5（双武器减值、术士/牧师目录、借机攻击表格），属 Stage 2 重排
  器优化范畴，非数据缺失。
- **答案级评测（MiMo）**：4 题 6 轮全部通过，事实正确率 100%、幻觉率 0%、无依据率 0%；
  工具调用 14 次在 12 预算内（含服务端恢复读取）。
- **Stage 1A Phase0 近重复只读审计**（`diversity_audit.py`）：85 题 Top-50，
  阈值 0.90。Top-8 重复占位共 16（平均 0.19/题，15/85 题有重复）、独立规则点平均
  7.69/8、桶内近重复 22 对、跨桶 22 对。结论：近重复温和，不构成 Top-8 挤占问题，
  进入 Phase1 多样性排序需另据收益评估，非当前阻塞。
- **Stage 1B 聚合指标**（`observability.py`）：每轮输出 JSON 指标行（阶段耗时、
  Token 估算、搜索/读取次数、裁剪消息、停止原因），不记录正文，见
  `services/app-python/src/trpg_app/observability.py`。
- 检索服务 60 项、app-python 90 项测试通过。

## release 环境核对

本机 release 工作树为 `/Users/nemo_xu/.codex/worktrees/7444/TrpgRuleAgent`，分支与
`origin/release` 同步（提交 `c40c818`）。该环境已配置 `.env`，并存在 ChromaDB：

- `data/libraries/pathfinder-1e/current` → `builds/20260801T134116Z`
- `data/libraries/pathfinder-1e/current/vector-index/chroma`
- MiMo 配置为 `mimo-v2.5`，密钥变量为 `MIMO_API_KEY`

release 的 `.env` 和 ChromaDB 已保留，current 已切换到最新候选。候选真实 MiMo 评测已使用 release 的
配置和环境变量、候选条目文档及候选 Chroma 索引运行；检索阶段进入了 thinking/searching/
reading/answering，但供应商流式回答在 120 秒整体评测超时内未完成，报告记录为
`model_timeout`。评测器已增加整体超时保护，报告位于候选修订 `audit/` 目录。

仍需完成长期集相关 ID 重映射、长期集/结构化集 Hit@5 验收、MiMo 答案验收，以及 release/main 的远端推送；本地 current 切换和旧 PF1e 修订清理已完成。
