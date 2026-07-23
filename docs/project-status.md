# TrpgRuleAgent 项目现状

更新时间：2026-07-24

本文档用于记录当前实现状态、架构边界和后续迭代重点。后续开发前应先阅读本文，并同步更新其中的状态。

## 一句话概览

TrpgRuleAgent 是一个面向 TRPG 规则问答的独立 Agent 项目。它不再依赖 pi-agent，Node.js 运行时只使用内置能力；模型采用 BYOK（用户自行填写模型 Base URL、模型 ID 和 API Key），规则检索支持本地服务或云端 HTTP 服务。

## 当前发布状态

- 主开发基线：`main`
- 当前整合分支：`release`
- `release` 已合并所有已完成阶段，并已推送远程仓库。
- 当前版本仍属于第一版工程基线，尚未建立正式语义化版本号和生产发布流水线。
- `main` 不应直接开发；新阶段应从合适的基线创建 `codex/*` 分支，完成后合并到 `release`。

## 已完成能力

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

### 本地演示

```bash
npm install
npm run retrieval:dev
npm run gateway
npm run web:dev
```

Gateway 默认监听 `127.0.0.1:8787`，本地检索服务默认监听 `127.0.0.1:8765`。生产或云端检索部署时，设置 `RETRIEVAL_BASE_URL`；需要鉴权时设置 `RETRIEVAL_API_KEY`。

### 常用验证

```bash
npx tsc --noEmit
npm run web:typecheck
npm run web:build
npm run test:python
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

1. 检索质量：结构化切块、重排器和漏召回题优化。
2. 答案级评测：事实、引用、工具预算和无依据结论率。
3. 规则导入审计：重复内容和异常编码检测。
4. Web/小程序产品化：登录、会话列表、模型配置管理和正式发布工程。
5. 生产基础设施：Redis/数据库 SessionStore、多实例共享限流、监控和 tracing。
6. 领域工作流：车卡、确定性属性计算、合法性校验和通用 Rule Pack SDK。
7. 云端检索具体实现：目前是协议兼容层，尚未绑定具体云向量数据库厂商。

## 后续开发约定

- 每个阶段先明确目标和验收标准，再从 `release` 创建独立 `codex/*` 分支。
- 一个阶段完成后运行与改动相关的类型检查、测试和构建。
- 提交信息使用清晰的英文 Conventional Commit 风格。
- 阶段完成后推送分支，并在合并到 `release` 后更新本文档的“已完成能力”和“尚未完成重点”。
- 不把用户 API Key、检索服务令牌、规则正文或本地索引提交到 Git。
