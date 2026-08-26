# TrpgRuleAgent 项目现状

更新时间：2026-08-13

本文档用于记录当前实现状态、架构边界和后续迭代重点。后续开发前应先阅读本文，并同步更新其中的状态。

## 一句话概览

TrpgRuleAgent 当前是一套面向少量可信用户的多游戏系统 Web 规则问答应用：React Web 是正式入口，Python 服务统一负责共享密码登录、服务端多模型配置、规则检索、证据引用与来源预览。旧 Node/BYOK Gateway 和微信小程序代码仍保留作为遗留实现，待新链路实际试用验收后一次删除。

## 当前发布状态

- `main` 与 `release` 已包含完成验收的 V1 Web/Python 基线。
- 新阶段从 `release` 创建独立 `codex/*` 分支，验收后再合并。
- 第二套真实规则库和跨系统隔离已完成本地验收。
- 当前版本仍属于第一版工程基线，尚未建立正式语义化版本号和生产发布流水线。
- `main` 不应直接开发；新阶段应从合适的基线创建 `codex/*` 分支，完成后合并到 `release`。

## 已完成能力

### 当前正式 Web/Python 链路

- `apps/web-next` 提供 React Web，支持桌面与手机浏览器。
- `services/app-python` 提供共享密码登录、HTTP-only 签名会话、SSE 聊天、来源读取和静态 Web 托管。
- 模型由服务端 YAML 配置，API Key 只从环境变量读取；客户端不能提交或查看模型密钥。
- 已接入 SenseNova `deepseek-v4-flash`、Xiaomi `mimo-v2.5` 和 `agnes-2.5-flash`。
- 每个模型可独立配置上下文窗口、最大输出、请求超时和最多重试次数。
- Agnes 使用官方 `chat_template_kwargs.enable_thinking=false` 原生关闭 thinking。
- 错误响应提供稳定错误码、是否可重试和请求 ID；安全日志不记录问题正文、回答或密钥。
- `/health` 检查进程存活，`/ready` 检查已发布规则库和模型配置是否可用。
- 模型必须读取规则证据后才能回答；漏写内联脚注时会补充实际读取的来源标签。
- 服务端统一串行执行工具调用，并对重复查询、重复结果、重复读取、连续无效调用、只搜索不读取和总决策上限进行收敛控制；达到总上限时会使用已读来源作答或安全返回证据不足，不再抛出工具循环错误。
- 取证和最终回答使用独立消息：最终阶段只接收用户问题与已读证据，不会把“继续搜索”等模型中间计划显示成答案；模型只搜索不读取或第三轮扩展搜索后，由服务端补读候选并收尾，最终最多携带 8 个章节。
- 模型首次跳过工具时，服务端会使用最后一条用户问题执行检索；模型提交不存在的文档 ID 时，服务端改读最近一次搜索的真实候选，因此多轮追问不再因 provider 工具行为差异返回 `evidence_missing`。
- 模型重复读取已注册章节时，服务端会继续读取同轮搜索中尚未读取的候选，再生成答案，避免用局部证据过早结束。
- 工具步骤记录请求、模型、规则库、预算、停止原因和查询摘要哈希，不记录用户问题正文。
- Agent 系统提示包含当前绑定规则库的系统、版本和当前发布修订；明显属于其他系统的问题不会用模型记忆作答。
- 桌面和手机端的新建对话均要求明确选择规则库，不再默认使用第一个规则库。
- 对话固定绑定系统与版本并记录创建时修订；规则库更新后，界面会警告旧回答的来源可能失效。
- 对话仅保存在当前浏览器 `localStorage`，服务端不持久化原始问题和回答。

### 当前规则库

- PF1E 中文规则库当前版本为 `20260809T004148Z`（V2.2 Stage 0 修复后重建，
  修复 CRB 战斗规则锚点章节切分与 table 策略页正文丢失），共 7140 个结构化文档、
  60683 个检索子块；旧 `20260805T084935Z` 保留为回滚点。
- 85 题长期混合检索回归为 Hit@5 90.6%、MRR 0.7351；30 题结构化专项集为
  Hit@5 83.3%、MRR 0.6750（此前 8/5 候选在 ID 未重映射下不可比；本轮相关 ID
  100% 覆盖后可计算）。答案级评测 MiMo 6/6 轮通过、事实正确率 100%、幻觉率 0%。
- V2.2 Stage 1 近重复 Phase0 只读审计、安全否决信号与 Context/Token/Evidence/Latency
  指标/报告工具已落地；数据评审决定当前不进入 Phase1，见 `near-duplicate-retrieval.md` §9、
  `observability.md`。MiMo 6 轮真实指标基线已完成，usage 覆盖率 100%；
  总延迟 mean/p95 为 57.35/75.24 秒，累计 prompt token mean/p95 为 78,332/147,689。
- V2.2 Stage 3 第一阶段的同轮 ContextBudget 已完成：工具结果完整展示一次，后续决策只保留
  ID/引用回执；搜索候选剔除结构化全文等检索内部字段；History 与 Evidence 改为 token 预算。
  同一 PF1E/MiMo 6 轮发布门为 6/6，usage 覆盖率 100%；累计 prompt token mean/p95 降至
  11,528/17,571，总延迟 mean 为 57.26 秒。滚动摘要仍暂缓。
- V2.3 Stage 0 已落地 12/24/48 轮长会话与 revision 确定性评测：3/3 case 通过，状态保留和
  显式覆盖正确率 100%，未确认状态污染率 0%，revision 与 Query Rewrite probe 正确率 100%；
  48 轮合成历史在确定性预算探针中为 726 token。经明确授权，真实 MiMo 长历史答案基线也已
  完成：3/3 轮通过、来源命中 3/3、无依据率 0%，工具调用总数 7、单轮最多 3；真实 Context
  记录的 History 为 222/391/737 token，三轮均未裁剪。Stage 0 发布门已满足，数据不支持启动 Summary。
- V2.3 Stage 1 已完成：动态 Evidence Policy、默认关闭的 Feature Flag、Compare/Build 多主题
  配额保护、指代追问恢复、`requiredSourceGroups` 专项评分和安全指标已落地。PF1E 85/30 题与
  GSS 22 题检索保持原基线；经明确授权，MiMo 固定/动态专项均为 5/5，动态 PF1E 回归为
  6/6、来源 100%、无依据率 0%。动态 6 轮 prompt token mean 10,924.5、总延迟 mean
  40.12 秒，未超过固定基线 11,528/57.26 秒。Feature Flag 继续默认关闭，等待发布流程决策。
- V2.3 Stage 2 已完成并通过发布门：纯代码 `RouteDecision`、最多 4 个子问题的受限拆解、默认关闭的
  `enable_query_decomposition`、串行取证恢复、子问题独立 Evidence 配额/来源集合和全局硬上限均已
  接入正式 Python 链路。12 题确定性专项集为 12/12，单问题误拆率 0%、目标域覆盖率 100%，
  Python App 156 项测试通过；经明确授权，真实 MiMo 3 题固定/拆解 A/B 为 2/3 → 3/3，
  无依据率 33.33% → 0%，最大工具调用 7 → 8（预算 12）；拆解开关 PF1E 6 轮回归为 6/6。
  原始报告仅保存在 `/private/tmp`，未提交仓库。
- V2.3 Stage 2.5 已修复最终回答一次性出现的问题：后端仅保留 96 字符安全前导，通过后在供应商
  流结束前持续发送 `text_delta`；SSE 明确禁用缓存转换和反向代理缓冲。首段拒答重试与无虚假
  来源保护保持不变。输入框同时补齐 IME 组合态保护：选字 Enter 与 WebKit process key 不发送，
  Shift+Enter 换行、普通 Enter 发送。服务已重启加载该实现。
- V2.3 Stage 3 启动条件复核已完成：10 个纯合成 Complex 结构案例 10/10，识别出 4 个
  Stage 2 足够案例与 6 个依赖/条件 Planner 候选。真实基线后的金集审计发现专长链来源误指向
  相邻矮人专长表；修正后 8 个证据探针 Hit@5 为 7/8、MRR 0.8125，Stage 2 有效来源组覆盖为
  5/6，原 12 题路由回归保持 12/12。经明确授权完成 6 题真实 MiMo Stage 2 基线：旧金集确定性
  门 4/6、报告来源命中 6/6、无依据率 0%，工具调用总计 46、单题最大 8/12。严格任务完成复核
  为 0/6；除专长链检索缺口外，其余失败仍覆盖条件判断、依赖传播和完整目标，因此受限 Planner
  启动条件成立。现已修复专长链查询，8 个证据探针恢复 8/8、MRR 0.875；Planner v1 Schema、
  Validator、串行 Executor、任务结果和最终合成输入已接入，六题本地生产路径来源组 6/6、全部
  计划任务完成。经再次明确授权，修正后的同六题真实 MiMo Stage 2/Planner A/B 自动门均为 5/6，
  严格任务完成率仍为 0/6 → 0/6；Planner 32/32 任务完成、0 fallback，但工具调用 46 → 52、平均
  端到端时延 93.89s → 119.12s、总模型 token 243,239 → 280,942。发布门未通过，Feature Flag
  继续默认关闭。下一阶段已加入确定性合成契约，覆盖条件分支、等级算术、专长资格、法术成长、
  装备数值与未决输入；升级后的结构评测保持 10/10，6 个 Planner 候选契约全部有效，下一动作
  为本地合成回归。该结果尚不代表真实答案质量改善，重新执行 MiMo A/B 仍需单独授权。
  经明确授权完成合成契约版同六题真实 A/B：契约 6/6 启用、26 项检查有 Evidence、1 个未决输入
  被识别，32/32 任务完成、0 fallback，但自动门仍为 5/6 → 5/6、严格完成率仍为 0/6 → 0/6；
  工具调用 46 → 52、平均时延 87.81s → 111.51s、模型总 token 244,592 → 290,132。发布门再次
  失败，下一步转向服务器控制的类型化 Fact Ledger 和流式输出前验证，不继续增加提示文本。
  Stage 3.2 第一版 Fact Ledger 已接入默认关闭的 Planner 路径：从已注册 Evidence 解析法师法术表、
  奖励专长范围、进阶要求、专长和装备数值，并在候选文本流出前验证。上一轮六个真实 MiMo 错误
  答案负样本回放 6/6 被拒绝；错误首稿不会泄露，第二稿通过后按最多 24 字符增量输出，连续两次
  失败则不附来源。当时的本地下一门为真实 Fact Ledger A/B，Feature Flag 保持关闭。
  经明确授权完成真实复测：同日合成契约 Planner 对照自动门 5/6、严格 0/6；规范 Fact Ledger
  实验组自动门 3/6、严格仍为 0/6，3 题安全拒答。平均时延 111.51s → 153.75s、模型总 token
  290,132 → 437,424，工具调用保持 52；PF1E 动态预算 6 轮回归仍为 6/6。发布门失败，下一步为
  §8.7 Stage 3 发布门整改。2026-08-12 已完成通用 Fact Ledger 核心、严格三元组注册表、PF1E
  Adapter 等价迁移和 GSS/失败安全降级；独立 `enable_fact_ledger` 默认关闭。2026-08-13 又完成
  PF1E Adapter v2 的专长时间线、前提/BAB 和法术元数据覆盖，三个已知漏判形状均进入确定性
  拒绝门。随后完成通用 `AnswerDraft` / `RepairPatch`、只修改失败 claim 的一次局部修补、完整
  重验证和确定性 Renderer；失败候选不流出，安全拒答作为独立 SSE/评测指标且不附来源。下一步
  的全仓回归、本地成本探针和发布审计已通过（App 221、Retrieval 64、Vitest 268、两套类型检查
  和两套构建）；无修补 1 次、触发修补最多 2 次模型调用。真实 MiMo A/B 仍需另行授权，两个
  Feature Flag 继续默认关闭。2026-08-14 经授权完成同六题真实 A/B：Planner 对照自动门 4/6，
  结构化 Fact Ledger 为 0/6、6/6 安全拒答，实际 patch 为 0；平均延迟增加 29.49%，平均模型
  token 增加 4.75%。安全边界有效但发布门失败，下一步回到本地修复 claim path/schema 契约，
  确定性 patch 成功金线通过前不再运行外部 A/B。
- MiMo 已完成 4 组、6 轮 PF1E 真实问答验收；事实、来源和多轮追问均通过。按本轮决定未重复验收 Agnes 与 SenseNova。
- 《夕妖晚谣》1.2 中文规则库已发布 154 个父文档、488 个检索子块。
- 当前发布版本为 `20260801T105530Z`，包含 `GSS` 与 `Golden Sky Stories` 显式别名。
- 22 题混合检索验收为 Hit@5 100%、MRR 0.9773。
- 两套规则库的检索、回答和来源读取均按 `libraryId` 隔离。
- 文本型 PDF 和 CHM 可由管理员命令导入；构建与原子发布分离。
- 发布构建会保留 PDF/CHM 提取报告中的源文件、页数、表格统计和启发式警告；警告不代表完成了图片或复杂表格理解。

### V1 稳定性验收

- TypeScript/Vitest：28 个测试文件、268 项测试通过。
- Python 应用服务：196 项测试通过。
- Python 检索服务：64 项测试通过。
- 全仓 TypeScript 类型检查和 Web 生产构建通过。
- 三个真实模型均完成 PF1E 和《夕妖晚谣》检索、回答、来源和结束事件验收。
- Agnes、MiMo、DeepSeek 已用同一无明确归属问题完成工具收敛回归：无证据结果安全结束，已读取结果只引用真实来源。
- 390×844 手机视口完成对话切换、模型选择、Markdown、输入区和来源抽屉验收。

以下章节记录的是仍保留在仓库中的旧 Node/BYOK 能力，不再代表当前正式产品入口。

### Agent Runtime

- 自有 Agent Runtime，零运行时第三方依赖。
- 内部消息、事件、工具注册、工具循环、预算和错误模型已拆分。
- `ModelProvider` 为可扩展接口，当前实现为 OpenAI-compatible 流式 Provider。
- Provider 支持流式文本、工具调用、SSE 分片和错误脱敏。
- API Key 只通过单次运行上下文传入，不写入消息、会话、日志或事件。

### 规则 Agent 与检索

- 已实现 `search_rules`、`read_rules` 工具和 Citation Registry。
- 检索服务协议覆盖健康检查、搜索、文档读取和来源列表。
- `RulesProvider` 是检索抽象；`RulesClient` 是零依赖 HTTP 实现。
- `RETRIEVAL_BASE_URL` 可以指向本地 Python 服务或云端向量/混合检索服务。
- 支持服务端 `RETRIEVAL_API_KEY`，仅用于 Gateway 到检索服务的鉴权。
- Rules Client 已具备超时、取消、网络错误、无效响应和 HTTP 错误归一化处理。

### BYOK Gateway

- 会话创建时绑定 provider、model、baseUrl、rulesetId。
- 模型 API Key 只接受 `X-Model-Api-Key` 请求头，刷新或离开页面后由客户端重新填写。
- SessionStore 只保存非敏感配置、消息历史、版本和过期时间；token 只保存摘要。
- 会话 turn 具备并发互斥、失败回滚、输入限制、请求体限制和凭据字段拦截。
- 支持 CORS 白名单、模型 Base URL 白名单、单进程来源限流和安全响应头。
- `/health` 检查进程存活，`/ready` 检查检索依赖可用性。

### 客户端接入

- `packages/gateway-client` 提供浏览器和微信小程序可复用 SDK。
- `apps/web` 提供无框架 Web 调试 UI。
- `apps/miniprogram` 提供小程序宿主接入骨架、会话门面和示例页面。
- 客户端 token、API Key 均只保存在当前页面/宿主内存中。

## 目录职责

```text
packages/agent/          Agent 内核、Provider、规则 Agent
packages/rules-client/   RulesProvider 与 HTTP 检索客户端
packages/rules-types/    检索协议和规则数据类型
packages/gateway-client/ 浏览器/小程序 Gateway SDK
apps/gateway/            BYOK HTTP/SSE 网关
apps/web/                Web 调试 UI
apps/miniprogram/        微信小程序接入骨架
services/retrieval-python/ 本地规则导入、索引和检索服务
rulepacks/               规则集配置与演示 fixture
docs/                    架构、协议、客户端和项目状态文档
```

## 当前运行方式

### 当前 Web/Python 应用

```bash
npm install
npm run next:web:build
npm run next:server
```

应用默认监听 `127.0.0.1:8000`。配置参考 `config/app.example.yaml`，本地密钥只写入被 Git 忽略的 `.env`。

### 常用验证

```bash
npx tsc --noEmit
npm test -- --pool=forks --maxWorkers=1
npm run next:web:build
npm run next:test:python
PYTHONPATH=services/retrieval-python/src python3 -m unittest discover -s services/retrieval-python/tests -v
```

Vitest 在当前环境可能因并发 worker 或测试中的长连接而长时间不退出；定位单个测试文件时可使用：

```bash
npx vitest run <test-file> --pool=forks --maxWorkers=1
```

## 明确的安全边界

- 项目不读取或保存全局模型 API Key；`LLM_API_KEY` 只对 CLI 有效，Gateway 忽略它。
- 客户端不能覆盖 `retrievalBaseUrl`，也不能通过 URL query/body 传递凭据。
- `RETRIEVAL_API_KEY` 是 Gateway 服务端配置，不得放入 Web 或小程序环境变量。
- 模型 Base URL 默认需要 HTTPS 和白名单；本地 HTTP 只能显式开启开发开关。
- 多实例部署时，当前内存 SessionStore 和限流器不能直接作为最终生产方案。

## 尚未完成的重点

> 本节是 backlog，不代表实施顺序。当前唯一总体优先级、阶段门和验收标准见
> [V2.3 后续迭代执行方案](v2.3-iteration-plan.md)。

1. 管理员规则上传与发布页面；目前只有命令行工作流。
2. 同轮 ContextBudget 与工具结果压缩已完成；长会话/revision 确定性评测和真实 MiMo 长历史
   答案基线均已通过。上下文摘要仍未实现；当前最长 48 轮样本仅 737 History token 且未裁剪，
   不满足 Summary 启动条件。
3. 动态 Evidence Budget Stage 1 与受限多问题拆解 Stage 2 均已完成真实 MiMo A/B、回归和发布门。
   Stage 3 受限 Planner 已满足启动条件但连续真实质量门失败，Feature Flag 保持关闭；§8.7 的
   通用核心、PF1E Adapter 等价迁移、失败样本覆盖、结构化局部修补及本地全门已经完成；真实
   MiMo A/B 曾因 6/6 安全拒答而失败。服务器拥有的 claim path/schema 契约、本地真实输出形状
   回放和隐私安全失败分类现已完成，确定性一次 patch/full revalidation 金线通过。经再次授权的
   同六题真实 A/B 中，Planner 对照自动门 6/6、严格 0/6；路径契约 Fact Ledger 为 0/6、4/6
   安全拒答，1 次修补尝试因 `path_not_found` 未应用，严格仍为 0/6。下一步回到本地固定本轮
   无效草稿、path 未命中和局部答案形状。现已在本地完成 Adapter v3 / PF1E Adapter v4：服务端
   强制任务所需精确路径、使用合法 JSON 示例、仅允许唯一的结构/具名映射，并以受限枚举聚合解析
   失败；Python App 230、Retrieval 64、Vitest 268 及全部类型/构建门通过。经新的明确外发授权，
   同六题真实 A/B 为 Planner 自动 4/6、严格 0/6，required-path Fact Ledger 自动 1/6、严格
   0/6、4/6 安全拒答；2 次修补中 1 次应用 5 个操作但完整复验失败，另 1 次以
   `path_ambiguous` 拒绝。平均延迟增加 75.01%，模型 token 增加 25.80%。质量、误拒和成本门仍
   失败，两个 Flag 继续默认关闭，仍不进入 Summary 或并行。其后的纯本地整改已将 PF1E Adapter
   升至 v5：服务端确定性消歧重复内部 ID，按证据发布逐级法术必需路径，识别紧凑/显式列标签法术
   位表，并脱敏记录 patch 后残余 issue/template。Python App 233、Retrieval 64、Vitest 268 及
   全部类型/构建门通过。经再次授权的 v5 同六题真实 A/B 为 Planner 自动 5/6、严格 0/6，
   Fact Ledger 自动 0/6、严格 0/6、5/6 安全拒答。紧凑法术表的 `spell_slot_count` 错误成功
   检出，但受限 sibling path 无法映射；3 次实际修补调用全部因 patch Schema 不合格而拒绝。
   平均延迟增加 71.76%，模型 token 增加 62.13%。两个 Flag 继续默认关闭，下一步回到本地
   收缩修补协议。2026-08-15 已完成该纯本地整改：Adapter 协议 v4 / PF1E Adapter v6 增加仅限
   `spell_slot_count` 的确定性 sibling mapping；重复路径独立分类为 `duplicate_path`；修补请求改为
   逐目标完整 operation 骨架，服务器固定目标、issue code 与 evidence refs，并对无效 patch 形状
   做受限分类。Python App 237、Retrieval 64、Vitest 268 及全部类型/构建门通过；本轮未调用
   外部模型，两个 Flag 仍默认关闭。经 2026-08-18 明确授权的 v6 同六题真实 A/B，正式 Planner
   对照自动 5/6、严格 0/6，Fact Ledger 自动 0/6、严格 0/6、5/6 安全拒答。唯一一次真实
   5-operation patch 已合规解析并应用，但完整复验残留 `feat_timeline_slot_count`，仍未发布；四次
   草稿失败已分为 `invalid_json`、`invalid_path`、`duplicate_path`、`missing_required_path`。
   平均延迟增加 77.59%，模型 token 增加 25.79%。发布门继续失败，两个 Flag 保持关闭。
4. 答案级评测（已落地，见 `services/app-python/src/trpg_app/answer_evaluation.py` 与 `docs/answer-evaluation.md`）：事实点(`requiredAny`)、引用支持度(`source_match`)、工具预算(`toolCalls`/`withinBudget`)、无依据结论率(`unsupportedRate`)、**LLM-judge 事实正确性/幻觉(`factualCorrect`/`hallucinationFree`, 经 `--judge-model`)** 五项指标 + 多轮/错误/超时/judge 容错 + 单测。PF1E 已有 v2.0/v2.1 真实金标题集。前端仍保留 Agnes/SenseNova 可选，并将 MiMo 设为新对话默认模型；**Agnes / SenseNova 明确不在本轮评测范围**。待补：GSS 的真实金标题集。
5. 检索质量：结构化切块、重排器和剩余漏召回题优化。V2.2 Stage 0 已修复锚点章节切分（CRB 战斗规则 10 子章节恢复）与 table 策略页正文丢失（overview 兜底），候选重建为 7140 文档、质量门通过；85/30 题相关 ID 100% 覆盖，检索基线待确认。
6. PDF/CHM 图片、扫描件 OCR 和复杂表格理解。
7. 生产基础设施：HTTPS 反代、登录限流、监控、tracing、备份和正式发布流水线。
8. 实际试用验收后一次删除旧 Node Web/Gateway 和微信小程序实现。

## 后续开发约定

- 每个阶段先明确目标和验收标准，再从 `release` 创建独立 `codex/*` 分支。
- 一个阶段完成后运行与改动相关的类型检查、测试和构建。
- 提交信息使用清晰的英文 Conventional Commit 风格。
- 阶段完成后推送分支，并在合并到 `release` 后更新本文档的“已完成能力”和“尚未完成重点”。
- 不把用户 API Key、检索服务令牌、规则正文或本地索引提交到 Git。

## 2026-08-20 Stage 3 本地状态

Adapter 协议 v5 / PF1E Adapter v7 已完成本地整改：required claim 使用服务器给出的完整草稿骨架
与固定 canonical path，唯一 prose-wrapped JSON 可安全恢复，同码验证问题按精确目标隔离，受限
法术位 sibling mapping 已通过一次 patch 后完整复验并发布的端到端测试。全量结果为 Python App
242/242、Retrieval 64/64、Vitest 268/268、全部 TypeScript 检查及两套 Web 生产构建通过。
本轮未调用外部模型；Planner 与 Fact Ledger 仍默认关闭，新的真实 MiMo A/B 仍需明确授权。

随后经明确授权完成同六题 v7 真实 MiMo A/B：Planner 对照自动 5/6、来源 6/6、严格 0/6；
Fact Ledger 自动与严格均为 0/6，6/6 安全拒答。实验组 3 次应用合规 patch 后仍未通过完整复验；
另有目标映射、patch 字段和证据引用各一次受限失败。路径骨架问题已明显收敛，但质量、误拒与成本
门继续失败。两个 Feature Flag 保持默认关闭，下一步继续本地整改。

对应的纯本地整改已完成：Adapter 协议 v6 / PF1E Adapter v8 与 Repair Patch Schema v2 使用
服务器私有 claim path marker 精确锚定专长槽和非法具名选项，patch 只接受 replacement 文本数组，
固定字段由服务器重建；marker 注入会被拒绝且不会流入答案。全量结果为 Python App 246/246、
Retrieval 64/64、Vitest 268/268、全部 TypeScript 检查及两套 Web 构建通过。本轮没有调用外部
模型，Planner 与 Fact Ledger 仍默认关闭。

随后经明确授权完成 v8 同六题真实 MiMo A/B：Planner 对照自动 5/6、严格 0/6；Fact Ledger
自动 0/6、严格 0/6、5/6 安全拒答。两次 Patch v2 均成功应用，且无目标映射或固定字段失败，
但均未通过完整复验；另有三次草稿 evidence/schema 失败。发布门继续失败，下一步从自由文本
逐槽修补转向 Adapter-owned 类型化专长选择与服务器 Evidence 分配。两个 Feature Flag 保持关闭。

2026-08-22 已在纯本地完成该后续：Adapter 协议 v7 / PF1E Adapter v9 为全部 required claim
发布服务器 Evidence，并把逐级专长 claim 收缩为精确数量、已读有限域和确定性正文模板。模型不能
再用自由文本少写专长槽，也不能通过 required claim 的 Evidence 字段伪造或误选来源；越界、重复、
数量错误、契约覆盖偏差和未注册来源均安全拒绝。全量门为 Python App 251/251、Retrieval 64/64、
Vitest 268/268、全部 TypeScript 检查及两套 Web 构建通过。未调用外部模型，两个 Flag 继续默认
关闭。

随后经 2026-08-23 明确授权完成 v9 同六题真实 MiMo A/B：Planner 对照自动 5/6、来源 6/6、
严格 0/6；Fact Ledger 自动 0/6、来源 0/6、严格 0/6、6/6 安全拒答。所有实验草稿均以
`schema_fields` 在外层协议边界拒绝，未进入类型化选择验证或 Repair。工具调用均为 52，实验组
平均 token 增加 4.36%；发布门继续失败，两个 Feature Flag 保持默认关闭。下一阶段在本地将模型
wire output 收缩为最小 values object，其余结构全部由服务器拥有。

2026-08-24 已完成 v10 本地整改：Adapter 协议 v8 / PF1E Adapter v10 只要求模型输出固定顺序的
单字段 values 数组，服务器生成 section、标题、claim ID、canonical path、类型化专长正文和
Evidence。模型 S 标签不再决定来源；额外字段、错误数组形状/数量和非法选择具有独立受限分类。
全量门为 Python App 254/254、Retrieval 64/64、Vitest 268/268、全部 TypeScript 检查及两套 Web
构建通过。本轮未调用外部模型，两个 Feature Flag 保持默认关闭。

随后经明确外发授权完成 v10 同六题真实 MiMo A/B：Planner 对照自动 5/6、来源 6/6、严格
0/6；Fact Ledger 自动 0/6、来源 2/6、严格 0/6、4/6 安全拒答。实验组 5/6 通过最小 values
解析，2/6 发布，三题进入 Validator/Repair；一次修补成功应用 4 个 replacement 后完整复验
失败。协议可用性较 v9 明显提升，但两份发布答案仍不满足严格正确或任务完成标准。

工具调用均为 52，实验组平均 token 增加 43.68%、平均模型调用增加 33.33%；并行延迟只作
方向性参考。全部发布门仍失败，两个 Feature Flag 保持默认关闭。下一步回到纯本地，继续把
确定性法术进度和 Repair 输出收缩为服务器拥有的类型化契约。

2026-08-24 已完成 v11 本地整改：Adapter 协议 v9 / PF1E Adapter v11 将法师逐级最高环级与
基础每日法术位改为服务器确定性正文，相应模型 value 必须为 `null`；Repair Patch v3 的模型 wire
只保留 values 数组，并对专长修补复用服务器 exact count、有限 catalog、正文模板与 Evidence。
缺失等级 path 的奖励专长范围错误只能按唯一实际文本定位，歧义继续安全拒绝。

全量门为 Python App 259/259、Retrieval 64/64、Vitest 268/268、全部 TypeScript 检查、两套
Web 生产构建及 `git diff --check` 通过。本轮没有调用外部模型，两个 Feature Flag 保持默认关闭；
下一步是真实 v11 MiMo A/B，但必须重新取得明确外发授权。

随后经 2026-08-25 明确授权完成 v11 同六题真实 MiMo A/B：Planner 对照自动 5/6、来源 6/6、
严格 0/6；Fact Ledger 自动 1/6、来源 1/6、严格 0/6、5/6 安全拒答。服务器确定性法术进度在
唯一发布题中完全正确，但剩余自由文本 claim 仍可跨语义 topic 写入事实，造成服务器 Evidence
绑定与正文不匹配。另有草稿、空输出、复验和 Patch 边界失败。

本轮未生成逐 turn metrics，因此不报告 token、延迟或模型调用差值。全部发布门仍失败，两个
Feature Flag 保持默认关闭。下一步回到纯本地：服务器 claim 不再要求模型返回 null 槽，继续
类型化自由文本，并让评测报告自动生成 metrics。

2026-08-25 已完成 v12 本地整改，未调用外部模型。Adapter 协议 v10 / PF1E Adapter v12 已将
server-owned claim 从所有模型 values 中移除；build、conditional、equipment 文本带有服务器执行
的 topic 语义约束；Repair v3 兼容精确单层 required_output 包装；评测报告自动派生 metrics 与
observability 文件。Python App 262/262、Retrieval 64/64、Vitest 268/268、全部 TypeScript、两套
Web 构建及 `git diff --check` 通过。两个 Feature Flag 继续默认关闭；真实 v12 同六题 MiMo A/B
仍需针对本轮的明确外发授权。

随后经针对 v12 的明确授权完成同六题真实 A/B。Planner 对照自动 6/6、来源 6/6、严格 0/6；
Fact Ledger 自动 0/6、来源 1/6、严格 0/6、5/6 安全拒答。两题被 whole-string
`semantic_scope` 误拒，另有 draft/Repair JSON 和专长槽复验失败。实验平均 token +40.44%、
模型调用 +33.33%、平均延迟 +45.38%、p95 +95.08%，全部发布门失败。

2026-08-25 已完成 v13 纯本地整改，未再次调用外部模型。Adapter 协议 v11 / PF1E Adapter v13
使用服务器 topic 子句抽取、有序专长 selection groups、叙述式专长解析、证据不足的 server-owned
槽、奥法骑士确定性施法推进和各一次 draft/Repair JSON-only 恢复。新增指标只记录恢复布尔值及
contract 数量，不记录正文或 Evidence 内容。

Python App 268/268、Retrieval 64/64、Vitest 268/268、全部 TypeScript、两套 Web 构建及
`git diff --check` 通过。两个 Feature Flag 继续默认关闭；真实 v13 同六题 MiMo A/B 需要新的
明确外发授权。

2026-08-26 已完成 Codex 式连续任务上下文本地改造。角色 State 可稳定保留 5 级、猫族、通灵者、
虚空之声和 20 点购点；助手旧答案不会污染 State。用户明确要求检查上一版方案时，服务器会选择
一条相关历史草案并标记为不可信审查对象，即使普通历史随后被 Context Budget 裁剪也不会丢失。
PF1E 购点复核在完整注册证据支持下由服务器确定性计算，不再依赖模型心算。

新增真实缺陷回放后，长会话确定性评测为 4/4，所有状态、覆盖、污染隔离、检索改写和草案引用
指标均为 100%。经新增 payload 的明确授权，真实 MiMo 首轮发现模型仍会忽略购点总和；最终将
证据充分的购点审查改为服务器直接渲染，并收紧自动断言。相同 payload 复验严格通过：总消耗 14、
剩余 6、感知 12、魅力 14，两份金来源均命中，4 次工具调用且在预算内。Markdown 格式兼容修正后
本地重评分为 1/1。

最终本地门为 Python App 289/289、Retrieval 64/64、Vitest 268/268、两套 TypeScript 检查、
两套 Web 构建及 `git diff --check` 通过。

随后经明确授权完成 v13 真实 A/B。Planner 对照自动 5/6、来源 6/6、严格 0/6；实验组自动与来源
均为 1/6、严格 0/6、2/6 安全拒答，另有 3/6 模型超时。奥法骑士服务器施法推进首次在真实答案
中正确发布，但答案违反题目条件分支；两个完成的拒答 turn 仍为 `semantic_scope`。JSON-only 恢复
未触发，未进入 Repair。发布门继续失败，两个 Feature Flag 保持默认关闭；下一步回到纯本地 v14。

v14 纯本地整改已完成，未再次外发。Adapter 协议 v12 / PF1E Adapter v14 允许有边界的自由文本
claim 在没有合法 topic 子句时使用服务器“证据不足、不臆测”fallback，避免一个自由文本值导致
整题 `semantic_scope` 拒答；应用次数进入隐私安全 metrics。结构化生成的证据正文限制为 48,000
字符并公平覆盖所有已注册来源，服务器 Fact Ledger 的构建和验证仍使用完整证据，普通 Planner
路径不变。

Python App 270/270、Retrieval 64/64、Vitest 268/268、两套 TypeScript、两套 Web 构建及
`git diff --check` 通过。两个 Feature Flag 继续默认关闭；真实 v14 A/B 尚未运行且需要新的明确
外发授权。
