# TrpgRuleAgent

TrpgRuleAgent 是一个面向跑团规则的可扩展 Agent 平台。第一版聚焦 Pathfinder 1E，目标是提供基于证据的规则问答、结构化引用和可追踪的多轮工具调用。

> 项目不分发 Pathfinder 规则正文。使用者需要自行提供有权使用的 PF1E CHM 文件；源文件、解析结果和向量索引默认只保存在本机。

## 当前里程碑

第一条可运行纵向链路已经建立，并已接入本地 BGE 向量检索：

```text
CLI -> 自有 Agent Runtime（零第三方运行时依赖）
    -> ModelProvider（OpenAI-compatible Chat Completions 流式）
    -> search_rules/read_rules 工具
    -> Python Retrieval Service -> BGE + Chroma -> PF Rule Pack
```

Node 侧 Agent Core、规则 Agent 和 CLI 不依赖任何第三方运行时包，只使用 Node.js 22.19+ 内置的 fetch、AbortSignal 与 Web Streams；TypeScript、Vitest、tsx 仅作为开发依赖。ModelProvider 是可扩展接口，首版支持 OpenAI-compatible Chat Completions 协议（`LLM_PROVIDER=openai-compatible`）。

仓库内 Rule Pack 只有明确标注的演示数据，用于验证工程链路，不能作为真实 PF 规则依据。本地可从用户持有的 CHM 导入真实父文档；原始 CHM、解包文件和生成索引均位于 Git 忽略的 `data/` 目录。

## 目录

```text
apps/cli                       流式命令行入口
apps/gateway                   BYOK Agent Gateway（HTTP/SSE，模型 Key 由客户端每次请求携带，服务端零持久化）
apps/web                       基于 Vite 的浏览器调试 UI（零运行时依赖，静态产物；详见 [Web 客户端](docs/web-client.md)）
apps/miniprogram               微信小程序宿主接入骨架（非完整可发布工程；含适配边界 createWeChatAdapters 与页面级门面 MiniProgramSession；详见 [Web 客户端](docs/web-client.md) 与 apps/miniprogram/README.md）
packages/agent                 自有 Agent Runtime（core/ 业务无关内核、providers/ 模型接入、rule-agent/ 规则领域层）
packages/gateway-client        BYOK Gateway 共享客户端 SDK（零运行时依赖；内置 BrowserTransport 与可选的
                               微信小程序 WeChatTransport——不依赖 wx 全局，由宿主注入最小适配接口；
                               [Web 客户端](docs/web-client.md) 介绍）
packages/rules-client          检索服务客户端
packages/rules-types           跨语言接口对应的 TypeScript 类型
services/retrieval-python      Python 检索、索引与评测服务
rulepacks/pathfinder-1e        PF 规则包定义、演示资料和检索评测集
```

## 快速开始

### 环境要求

- Node.js 22.19+
- Python 3.9+
- PF1E CHM 文件（由使用者自行合法取得）
- CHM 解包工具：macOS 执行 `brew install chmlib`；Ubuntu 执行 `sudo apt-get install libchm-bin`
- 一个兼容 OpenAI Chat Completions 协议、并支持工具调用的模型

### 1. 克隆并一键构建规则索引

```bash
git clone https://github.com/Nemo979/TrpgRuleAgent.git
cd TrpgRuleAgent
npm run setup:pf -- "/absolute/path/to/Pathfinder.chm"
```

这个命令会自动安装 Node.js/Python 依赖、导入 CHM 并建立向量索引。首次下载嵌入模型和构建索引需要一些时间；过程可以断点续建。

### 2. 配置模型

安装脚本会创建 `.env`。填写模型端点和密钥：

```dotenv
LLM_PROVIDER=openai-compatible
LLM_MODEL=your-model-id
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=your-api-key
LLM_CONTEXT_WINDOW=128000
LLM_MAX_TOKENS=8192
LLM_REASONING=false
RETRIEVAL_BASE_URL=http://127.0.0.1:8765
RULESET_ID=pathfinder-1e
```

`LLM_MODEL`、`LLM_BASE_URL`、`LLM_API_KEY` 为必填项，缺失时 CLI 会给出明确的配置错误。`.env` 已被 Git 忽略。不要把真实密钥填写到 `.env.example` 或提交到版本控制。

### 3. 启动

一个命令同时启动检索服务和交互式 Agent：

```bash
npm start
```

直接输入问题即可；输入 `/exit` 退出。也可以执行单次问答：

```bash
npm start -- "什么时候会触发借机攻击？"
```

如果希望分别观察服务日志，仍可在两个终端分别运行 `npm run retrieval:pf` 和 `npm run cli`。

### 4.（可选）启动 BYOK Gateway

为浏览器前端提供 HTTP/SSE 接口：

```bash
npm run gateway
```

Gateway 采用 BYOK（Bring Your Own Key）模型：用户在创建会话时提交自己的模型连接配置（`provider`/`model`/`baseUrl`/`rulesetId`，经服务端白名单严格校验），每个会话用自己的配置创建 Agent；服务端不读取 `LLM_API_KEY`/`LLM_MODEL`/`LLM_BASE_URL`，模型 Key 由前端页面内存持有并通过每次请求的 `X-Model-Api-Key` 头传入，服务端不落任何持久化。接口协议与安全边界详见 [Gateway API](docs/gateway-api.md)。

### 5.（可选）启动 Web 调试 UI

浏览器调试界面基于共享 SDK `@trpg-rule-agent/gateway-client`（零运行时依赖），构建产物为纯静态文件：

```bash
# 开发服务器（默认 http://localhost:5173）
npm run web:dev
# 构建静态产物到 apps/web/dist
npm run web:build
# 本地预览构建产物
npm run web:preview
```

Web UI 默认走真实 Gateway（同源相对路径，建议生产用同源反向代理避免 CORS 与凭据跨域）；仅当【开发模式（`import.meta.env.DEV`）+ URL 带 `?mock=1`】双条件同时满足时，才动态加载内置 mock 传输做无后端演示。该分支以 `import.meta.env.DEV` 静态门控被 tree-shake，因此**生产构建 / `web:preview` 中 `?mock=1` 无效，且产物不含任何 mock 代码**。API Key 与 session token 只在浏览器内存、刷新即丢失，绝不写入 `localStorage`/`cookie` 等。启动方式、安全模型、CORS 与同源反代部署详见 [Web 客户端](docs/web-client.md)。

SDK 另内置可选的 `WeChatTransport`，并由 `apps/miniprogram` 提供宿主接入骨架（非完整可发布工程）：其中包含 `createWeChatAdapters`（`wx-host.ts`，把宿主注入的 `wx.request` 封装成 SDK 需要的最小适配接口）与页面级会话门面 `MiniProgramSession`（`session-facade.ts`，负责内存态凭据与生命周期清理）。开发者需在微信开发者工具中自行创建小程序工程、粘贴/绑定 `wx` 适配器、配置 request 合法域名与 HTTPS；错误归一化与“API Key 仅随 `runTurn` 每次经请求头传入、绝不落盘”的边界与浏览器端完全一致。接入说明见 [Web 客户端](docs/web-client.md) 的“微信小程序接入”与 [apps/miniprogram/README.md](apps/miniprogram/README.md)。

## 演示模式（无需规则文件）

仓库包含少量明确标注的合成演示数据，只用于验证调用链路，不能作为真实 PF 规则依据：

```bash
npm install --ignore-scripts
cp .env.example .env
# 编辑 .env 后，在终端一运行：
npm run retrieval:dev
# 在终端二运行：
npm run cli
```

## 数据规模与评测

当前验证过的本地数据规模为 2,140 篇父文档、29,629 个向量子块。BGE 模型为 512 维，本地 Chroma 索引约 380MB。所有生成数据位于 Git 忽略的 `data/` 目录，详见 [数据边界](docs/data-policy.md)。

验证真实规则检索质量：

```bash
npm run eval:retrieval:pf
```

评测会报告 Hit@5、MRR 和每道题的首条规则路径，并将明细写入本地 `data/pathfinder-1e/generated/retrieval-eval.json`。

当前 85 题人工标注评测中，纯向量为 Hit@5 78.8% / MRR 0.638，BM25 + 向量混合召回为 Hit@5 96.5% / MRR 0.821。评测包含 77 道核心规则题和 8 道明确询问可选资料的题目；它用于检索回归，不等同于最终答案准确率。详见 [检索评测](docs/retrieval-evaluation.md)。

## 开发与验证

```bash
npm run check
npm run test:python
npm run eval:retrieval:pf:vector
npm run eval:retrieval:pf
```

## 安全与许可

- `.env`、`data/`、`.venv/`、模型缓存和向量索引均不会提交。
- GitHub 仓库只包含合成演示文本，不包含 PF1E 规则正文。
- 使用者负责确认其规则资料、模型服务和生成内容的使用权限。
- 源代码使用 [MIT License](LICENSE)。规则资料不属于本许可证授权范围。

## 第一版边界

- 只支持 `pathfinder-1e` Rule Pack。
- Agent 每次提问最多搜索 3 次、读取 8 篇规则文档、执行 8 次规则工具。
- 搜索工具只返回摘要；完整父文档必须通过 `read_rules` 按需读取。
- 每篇已读取文档获得稳定引用编号，例如 `[S1]`。
- 最终来源列表由程序持有的引用注册表生成，而不是依赖模型编造路径。

## 下一步

1. 为当前 3 道检索漏召回题增加结构化章节切块或重排器实验。
2. 增加答案级评测，验证事实、引用、工具预算和无依据结论率。
3. 为导入报告增加重复内容和异常编码审计。
4. 在 BYOK Gateway 之上增加 React 调试界面和 Agent Trace。
5. 增加车卡工作流和确定性合法性校验器，再抽取通用 Rule Pack SDK。
