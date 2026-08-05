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
