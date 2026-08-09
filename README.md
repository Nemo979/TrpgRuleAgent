# TrpgRuleAgent

TrpgRuleAgent 是一个面向跑团规则的可扩展 Agent 平台。当前版本支持彼此隔离的多游戏系统规则库，目标是提供基于证据的规则问答、结构化引用和可追踪的多轮工具调用。

> 项目不分发商业规则正文。使用者需要自行提供有权使用的 PDF 或 CHM；源文件、解析结果和向量索引默认只保存在本机。

## 当前正式链路

V1 采用单体 Web/Python 架构，面向少量可信用户：

```text
React Web
  -> 共享密码 + HTTP-only 签名会话
  -> Python SSE 规则 Agent
  -> 服务端配置的多个 OpenAI-compatible 模型
  -> search_rules/read_rules
  -> BM25 + BGE/Chroma + RRF
  -> 独立的“游戏系统 + 版本”规则库
```

模型 ID、Base URL 和 API Key 均由管理员在服务端配置；浏览器不能提交或读取模型密钥。每个对话固定绑定一个规则库，对话内容只保存在当前浏览器。当前已验证 SenseNova、Xiaomi MiMo 和 Agnes 三个模型。

仓库内 Rule Pack 只保存规则库元数据、合成演示数据和不含规则正文的评测题。本地可从用户持有的 PDF 或 CHM 导入真实父文档；原文件、解析结果和生成索引均位于 Git 忽略的 `data/` 目录。

旧 Node CLI/BYOK Gateway、无框架 Web 和微信小程序骨架仍暂时保留，待当前链路完成实际试用后一次删除。

## 目录

项目当前状态、架构边界和后续迭代计划见 [项目现状](docs/project-status.md)。

```text
apps/web-next                  当前 React Web 正式入口
services/app-python            当前 Python 应用服务、规则库管理命令和文件导入器
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
rulepacks/golden-sky-stories-zh-1-2
                              《夕妖晚谣》规则包元数据和检索评测集
```

## 当前 Web 版快速开始

### 环境要求

- Node.js 22.19+
- Python 3.11+
- 文本型 PDF 或 CHM 文件（由使用者自行合法取得）
- 导入 CHM 时需要解包工具：macOS 执行 `brew install chmlib`；Ubuntu 执行 `sudo apt-get install libchm-bin`
- 一个兼容 OpenAI Chat Completions 协议、并支持工具调用的模型

### 1. 安装依赖

```bash
git clone https://github.com/Nemo979/TrpgRuleAgent.git
cd TrpgRuleAgent
npm install --ignore-scripts
python3.12 -m venv .venv312
.venv312/bin/python -m pip install -e services/retrieval-python -e services/app-python
.venv312/bin/python -m pip install -r services/retrieval-python/requirements-vector.txt
```

### 2. 配置服务

复制配置模板：

```bash
cp config/app.example.yaml config/app.yaml
cp .env.example .env
```

在 `config/app.yaml` 中配置模型，在 `.env` 中填写共享密码、会话签名密钥和各模型 API Key。两者均被 Git 忽略；不要把真实凭据写入示例文件。

### 3. 准备规则库

管理员通过 `trpg-library extract-chm` 或 `extract-pdf` 提取文本规则，再使用 `build-jsonl` 和 `publish` 分阶段构建、原子发布。完整命令见 [Web/Python 迁移与管理员命令记录](docs/refactor-target.md)。

规则正文、解析结果和向量索引不会提交到仓库。

### 4. 构建并启动

```bash
npm run next:web:build
npm run next:server
```

默认访问地址为 `http://127.0.0.1:8000/`。服务探针：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

## 遗留 CLI/BYOK 链路

以下入口仅为验收后删除旧实现前的兼容保留，不再是正式产品入口。

### 启动 BYOK Gateway

为浏览器前端提供 HTTP/SSE 接口：

```bash
npm run gateway
```

Gateway 采用 BYOK（Bring Your Own Key）模型：用户在创建会话时提交自己的模型连接配置（`provider`/`model`/`baseUrl`/`rulesetId`，经服务端白名单严格校验），每个会话用自己的配置创建 Agent；服务端不读取 `LLM_API_KEY`/`LLM_MODEL`/`LLM_BASE_URL`，模型 Key 由前端页面内存持有并通过每次请求的 `X-Model-Api-Key` 头传入，服务端不落任何持久化。接口协议与安全边界详见 [Gateway API](docs/gateway-api.md)。

### 启动旧 Web 调试 UI

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

当前已验证两个完全不同的游戏系统，共 2,288 篇父文档、30,892 个向量子块。所有生成数据位于 Git 忽略的 `data/` 目录，详见 [数据边界](docs/data-policy.md)。

验证真实规则检索质量：

```bash
npm run eval:retrieval:pf
npm run eval:retrieval:gss
```

评测会报告 Hit@5、MRR 和每道题的首条规则路径。PF1E 明细写入
`data/pathfinder-1e/generated/retrieval-eval.json`，《夕妖晚谣》明细写入
`data/imports/golden-sky-stories-zh-1-2/retrieval-eval.json`。

PF1E 的 85 题长期评测中，结构化 BM25 + 向量混合召回为 Hit@5 100% / MRR 0.8676；30 题表格、子章节和职业变体专项集为 Hit@5 100% / MRR 0.9361。《夕妖晚谣》1.2 的 22 题验收集中，混合召回为 Hit@5 100% / MRR 0.9773。检索评测不等同于最终答案准确率。

## 开发与验证

```bash
npm test -- --pool=forks --maxWorkers=1
npx tsc --noEmit
npm run next:web:build
npm run next:test:python
PYTHONPATH=services/retrieval-python/src python3 -m unittest discover -s services/retrieval-python/tests -v
```

## 安全与许可

- `.env`、`data/`、`.venv/`、模型缓存和向量索引均不会提交。
- GitHub 仓库不包含 PF1E、《夕妖晚谣》或其他商业规则正文。
- 使用者负责确认其规则资料、模型服务和生成内容的使用权限。
- 源代码使用 [MIT License](LICENSE)。规则资料不属于本许可证授权范围。

## 当前第一版边界

- 当前本地已发布 `pathfinder-1e` 与 `golden-sky-stories-zh-1-2`，新对话必须明确选择规则库，创建后固定绑定系统与版本，并记录创建时的规则库修订；规则库更新时界面会提示旧回答的来源可能失效。
- Agent 当前每次提问最多搜索 6 次、读取 24 篇规则文档，并受 80,000 字符证据预算约束。
- 搜索工具只返回摘要；完整父文档必须通过 `read_rules` 按需读取。
- 每篇已读取文档获得稳定引用编号，例如 `[S1]`。
- 最终来源列表由程序持有的引用注册表生成，而不是依赖模型编造路径。
- 文本型 PDF 与 CHM 已支持；表格和图片相关警告仅是启发式提示，扫描件 OCR、图片和复杂图表理解暂不支持。

## 下一步

后续改造顺序以 [V2.3 后续迭代执行方案](docs/v2.3-iteration-plan.md) 为准：

1. 已完成可复现的 Stage 1 基线、近重复 Phase0 与 Context/Token/Evidence/Latency 测量。
2. 已完成 Stage 3 第一阶段的同轮 ContextBudget 和工具上下文压缩，并通过 MiMo 6/6 发布门。
3. 下一步先补长会话/revision 评测，再独立实现动态 Evidence Budget。
4. 随后增加受限多问题拆解；串行 Planner、滚动摘要和并行均受独立数据门控制。
