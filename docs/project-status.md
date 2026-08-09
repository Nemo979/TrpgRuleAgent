# TrpgRuleAgent 项目现状

更新时间：2026-08-09

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
- MiMo 已完成 4 组、6 轮 PF1E 真实问答验收；事实、来源和多轮追问均通过。按本轮决定未重复验收 Agnes 与 SenseNova。
- 《夕妖晚谣》1.2 中文规则库已发布 154 个父文档、488 个检索子块。
- 当前发布版本为 `20260801T105530Z`，包含 `GSS` 与 `Golden Sky Stories` 显式别名。
- 22 题混合检索验收为 Hit@5 100%、MRR 0.9773。
- 两套规则库的检索、回答和来源读取均按 `libraryId` 隔离。
- 文本型 PDF 和 CHM 可由管理员命令导入；构建与原子发布分离。
- 发布构建会保留 PDF/CHM 提取报告中的源文件、页数、表格统计和启发式警告；警告不代表完成了图片或复杂表格理解。

### V1 稳定性验收

- TypeScript/Vitest：27 个测试文件、263 项测试通过。
- Python 应用服务：105 项测试通过。
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

> 本节是 backlog，不代表实施顺序。V2.2 的唯一总体优先级、阶段门和验收标准见
> [V2.2 可行性与执行方案](v2.2-feasibility-plan.md)。

1. 管理员规则上传与发布页面；目前只有命令行工作流。
2. 同轮 ContextBudget 与工具结果压缩已完成。下一步先补长会话样本并验证 revision 失效边界；
   上下文摘要仍未实现，且当前历史 p95 仅 89 token，不得固定增加一次模型调用。
3. 动态检索预算；当前搜索、读取和证据字符上限仍是固定安全值。
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
