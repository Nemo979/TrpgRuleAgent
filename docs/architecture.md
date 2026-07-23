# Architecture

## 设计目标

第一版只支持 Pathfinder 1E，但核心模块不能依赖某一本规则书。增加规则集时，应以 Rule Pack、资料索引和领域工具为主，而不是复制 Agent。

Node 侧 Agent 采用项目自有的 Agent Runtime，零第三方运行时依赖：只使用 Node.js 22.19+ 内置的 fetch、AbortSignal、Web Streams 与标准语言能力。TypeScript、Vitest、tsx 仅作为开发依赖存在。

## 自有 Agent Runtime

`packages/agent` 按职责分为三层：

```text
packages/agent/src/
  core/         业务无关的 Agent 内核：消息模型、AgentEvent、ToolRegistry、
                JSON Schema 校验子集、AgentError、Agent Loop（AgentRuntime）
  providers/    ModelProvider 实现与注册表；首版为 OpenAI-compatible
                Chat Completions 流式 Provider（含零依赖 SSE 解析器）
  rule-agent/   TRPG 规则领域层：search_rules/read_rules 工具、工具预算、
                Citation Registry、系统 Prompt 与环境变量配置
```

### 消息模型与 Provider 边界

内部消息只有 `system`、`user`、`assistant`、`tool` 四种角色；assistant 消息携带完整文本、工具调用列表和可选 usage。Agent Loop 只消费内部 `ModelEvent`（`text_delta`、`tool_call_start`、`tool_call_arguments_delta`、`tool_call_end`、`usage`、`finish`），不感知任何厂商数据结构。

`ModelProvider` 是可扩展接口：Provider 负责把厂商流式协议转换为 `ModelEvent`。首版 Provider 通过 `POST {baseUrl}/chat/completions`（`stream=true`）访问 OpenAI-compatible 服务，SSE 解析支持跨 chunk 行、单 chunk 多事件、`[DONE]`、按 index 分片到达的 tool_calls/name/arguments。API Key 通过执行上下文传入 Provider，不写入消息、事件、日志或错误文本。`ProviderRegistry` 是扩展点，未来在 `providers/` 注册新实现即可。

### 工具循环与事件流

`AgentRuntime.run()` 返回 `AsyncIterable<AgentEvent>`，事件类型包括 `turn_start`、`text_delta`、`tool_start`、`tool_end`、`tool_error`、`turn_end`、`error`。流程为：加入用户消息 -> 调用 Provider -> 流式输出文本、聚合工具调用 -> 用内置 JSON Schema 子集校验参数（失败不执行工具）-> 顺序执行工具（保持引用编号与预算确定性）-> 写入 tool 消息 -> 再调模型，直到没有工具调用或达到最大模型轮次。AbortSignal 全程传递给模型请求与规则客户端。

错误统一为 `AgentError`，类别包括 `configuration_error`、`provider_http_error`、`provider_protocol_error`、`invalid_tool_call`、`unknown_tool`、`tool_execution_error`、`limit_exceeded`、`aborted`。Provider 对外抛出前会脱敏整个可观察错误链：顶层 `message` 与 `cause.message` 都会把 API Key 替换为 `[REDACTED]`；`cause` 为 Error 时只保留 name 与已脱敏 message（丢弃可能残留密钥的 stack、嵌套 cause），非 Error 的 cause 直接丢弃。这样即使日志打印完整错误链也不会泄露 API Key。首版不自动重试模型请求，避免工具重复执行。

CLI 只是 `AgentEvent` 的一个消费者；`apps/gateway` 是第二个消费者，把同一条事件流投影为 SSE 下发给浏览器，不需要改动 Agent 内核。

### 凭据边界（BYOK）

模型 API Key 不属于配置：`RuleAgentConfig` 只含非敏感项，凭据单独定义为 `RuleAgentCredentials`。`AgentRuntime.run(input, context)` 通过每次调用的 `AgentRunContext { apiKey, signal }` 获取 Key，仅用于构造一次 ProviderContext，不写入 options、messages、session 或事件。CLI 经 `loadRuleAgentCredentials` 从 `LLM_API_KEY` 读取；Gateway 则从每次请求的 `X-Model-Api-Key` 头读取，服务端零持久化。

每个 turn 开始时 Runtime 记录消息 checkpoint：provider/abort/limit 等终止性错误会回滚本轮全部新增消息，保证会话历史不残留半截 turn；`tool_error` 属于模型可自行恢复的流程，不触发回滚。

脱敏是纵深防御而非单点信任：内置 `OpenAICompatibleProvider` 通过 `redactAgentError` 对错误链脱敏，但 `ModelProvider` 是可扩展接口，自定义 Provider 未必如此。因此 Gateway 在 SSE 出口对 `error`/`tool_error` 的 `message` 基于本次 `X-Model-Api-Key` **再兜底脱敏一次**。此外，模型请求使用 `fetch(redirect:"error")` 禁止自动跟随 30x，避免允许端点重定向到内网绕过 Base URL 白名单（SSRF）。

## BYOK Agent Gateway

`apps/gateway` 只使用 Node 22 内置 `http`/`crypto`/`url` 与 workspace 包：

```text
apps/gateway/src/
  config.ts           环境变量 -> GatewayConfig（刻意不含凭据，也不含全局模型配置）
  credentials.ts      凭据边界：Key 仅限 X-Model-Api-Key 头；body/query 凭据字段一律 400
  session-config.ts   SessionCreateRequest 严格校验 -> SessionConnectionConfig
                      （provider/model/baseUrl/rulesetId；拒绝未知字段与 retrievalBaseUrl）
  endpoint-policy.ts  ModelEndpointPolicy 契约 + AllowlistModelEndpointPolicy
                      （HTTPS、禁 userinfo/query/fragment、origin+路径边界前缀匹配、
                      条目自校验、GATEWAY_ALLOW_LOCALHOST_MODEL 开发开关）
  session-store.ts    SessionStore 全异步接口 + InMemorySessionStore（token 只存 SHA-256
                      摘要、快照含连接配置/消息/版本、空闲 TTL 续期且进行中 turn 免删、
                      可注入时钟；未来可换 Redis/加密存储）
  sse.ts              AgentEvent -> 公共事件显式白名单投影（错误只留 category/message，
                      并在出口基于本次 apiKey 对 error/tool_error message 兜底脱敏、
                      不依赖 Provider 自觉；sources 含 documentId）
  service.ts          编排：会话（BYOK 连接配置绑定会话）、并发互斥（409）、
                      per-turn Key 注入、按会话配置建 Agent、成功才写回历史
  rate-limit.ts        单进程来源限流（生产多实例应在反向代理层做共享限流）
  server.ts / index.ts  路由与进程入口
```

协议与限制详见 [Gateway API](gateway-api.md)。核心不变量：模型 Key 的生命周期等于一次 turn 请求；模型连接配置（provider/model/baseUrl/rulesetId）由用户创建会话时提交、逐会话生效，`retrievalBaseUrl` 始终由服务端注入；SessionStore 不存在凭据字段；失败 turn 不污染会话历史。

### 检索服务部署抽象

`packages/rules-client` 暴露 `RulesProvider` 契约，`RulesClient` 是其零依赖 HTTP 实现。`RETRIEVAL_BASE_URL` 可以指向本地 Python 检索服务，也可以指向满足同一 JSON 接口的云端向量/混合检索服务；Gateway 和 Agent 不需要感知部署位置。云端部署可通过服务端注入 `RulesClientOptions.headers` 传递检索服务鉴权头，并由客户端统一提供超时、取消和无效响应处理。后续接入特定云向量数据库时，只需新增一个 `RulesProvider` 实现，不改变规则 Agent、Gateway API 或前端宿主。

### 阶段六：Gateway 生产基础加固

Gateway 增加了可配置的单进程来源限流：默认每个 `remoteAddress` 在 60 秒内最多 120 个非健康检查/非 OPTIONS 请求，超限返回 `429 rate_limited` 与 `Retry-After`。该机制只保护单进程资源，不假设多实例共享状态；多实例生产部署仍应在反向代理或 API Gateway 层配置共享限流。原有请求体、输入长度、CORS、BYOK 和错误脱敏边界保持不变。

## 阶段三：共享 Gateway 客户端 SDK 与 Web 调试 UI

在 Gateway 之上新增两套可复用产物：`packages/gateway-client`（共享 SDK）与 `apps/web`（调试 UI）。两者共同把“API Key 永不离开宿主内存”的 BYOK 边界延伸到浏览器/小程序等前端宿主。

### packages/gateway-client（分层）

SDK 零运行时依赖，只依赖 Gateway 的公开 wire 协议（不导入 `apps/gateway` 或 `packages/agent` 内部类型），按职责分为：

```text
packages/gateway-client/src/
  protocol.ts    公开线协议类型：GatewayEvent / GatewaySource / GatewayWireError /
                 SessionCreateOptions / SessionModelConfig / SessionCreateResponse
  errors.ts      GatewayClientError + GatewayClientErrorCode（typed 错误分类）、
                 redactSecret / [REDACTED] 兜底脱敏、isGatewayClientError 守卫
  sse.ts         零依赖 SSE 帧解析（parseSseFrames）+ 事件投影（decodeGatewayEvent），
                 未知字段丢弃、未知类型视为 protocol_error、error/tool_error 再兜底脱敏
  transport.ts   GatewayTransport 抽象（request + stream）+ TransportError
                 （只携带 network/aborted 归一化分类，绝不透出头/URL/cause）
  browser-transport.ts  BrowserTransport：fetch + ReadableStream 实现；
                 redirect:"error" 防 SSRF、credentials:"omit"
  wechat-transport.ts   WeChatTransport（可选）：微信小程序传输实现；不依赖 wx 全局，
                 由宿主注入最小适配接口（WeChatRequestAdapter / WeChatStreamAdapter），
                 把回调式分块推送适配为 AsyncIterable<Uint8Array>
  client.ts      GatewayClient：高层 API（createSession/runTurn/abort/
                 deleteSession/disconnect/reset/status），token 存于 #private 字段
```

`apps/miniprogram`（宿主接入骨架，非完整可发布工程；仅依赖 @trpg-rule-agent/gateway-client，不引入微信 SDK）：

```text
apps/miniprogram/src/
  wx-host.ts         宿主适配边界：定义最小结构化声明 WxHost / WxHostRequestOptions /
                     WxHostRequestTask（不引用 wx 全局），并导出 createWeChatAdapters(host)，
                     把宿主注入的 wx.request 封装为 SDK 需要的 WeChatRequestAdapter 与
                     WeChatStreamAdapter（保证“先 onHeaders 后首个 chunk”；宿主 fail/onError
                     错误对象在边界被丢弃，只向 SDK 传固定文案占位错误）
  session-facade.ts  页面级会话门面 MiniProgramSession：API Key 仅存 #private 内存字段，
                     绝不写入 wx Storage/URL/日志/持久化；生命周期（connect/send/stop/
                     newSession/destroy）负责内存凭据与状态清理
apps/miniprogram/test/  纯 Node/Vitest 单测（wx-adapter / session-facade / security），无需微信环境
apps/miniprogram/example/  可复制的 Page/WXML/WXSS 与 app.json 示例（非完整发布工程）
```

传输抽象 `GatewayTransport` 与 DOM 解耦：`GatewayClient` 只消费 `request`/`stream` 两个能力，具体传输可替换（浏览器 `BrowserTransport`、小程序 `WeChatTransport`、测试 mock），协议层与客户端逻辑完全复用。

小程序接入边界：`WeChatTransport` 保持与 `BrowserTransport` 相同的 Transport 契约——`AbortSignal` 取消归一化为 `TransportError("aborted")`、其余传输失败归一化为 `TransportError("network")`（错误绝不携带 URL/请求头/API Key/底层 cause）；API Key 仍只在 `GatewayClient.runTurn` 每次调用时经 `X-Model-Api-Key` 头传入，不写入 Transport 实例状态或任何持久化介质。该骨架（`apps/miniprogram`）已交付，分层为：Page（开发者创建）→ `MiniProgramSession`（会话门面：凭据内存态与生命周期清理）→ `GatewayClient`/`WeChatTransport`（协议复用，SSE 解析与错误分类零复制）→ `createWeChatAdapters`（宿主适配边界，唯一由开发者注入 `wx.request` 的位置）。强调：仓库仍不包含完整小程序工程；`wx` 错误对象在适配边界被丢弃、绝不透传；API Key / session token 不进入 Storage / URL / 日志。接入细节见 [Web 客户端文档](web-client.md)“微信小程序接入”与 apps/miniprogram/README.md。

### apps/web（state / controller / view 分离）

基于 Vite 的 vanilla TypeScript/CSS 调试台，Vite 仅作为 devDependency，构建产出 `apps/web/dist` 纯静态文件。内部遵循单向数据流：

```text
main.ts        装配：传输层 -> GatewayClient -> ChatController -> ChatView -> 状态机
state.ts       纯函数 reducer（流式累加、固化、工具时间线、来源、错误、重置），可单测
controller.ts  ChatController：把 UI 意图翻译为 GatewayClient 调用、把事件派发为 Action
view.ts        ChatView：只用 textContent 渲染模型输出，禁 innerHTML / Markdown 注入
mock-transport.ts  仅【开发模式 + ?mock=1】双条件下动态注入的 MockTransport（生产被 tree-shake，不参与生产、不弱化安全）
```

安全边界：API Key 与 session token 只存于页面内存变量，刷新即丢失；绝不写入 `localStorage`/`sessionStorage`/`IndexedDB`/`cookie`/DOM `dataset`，也不进 URL/日志/错误/`status()` 快照；模型输出只经 `textContent` 写入 DOM。默认走真实 `BrowserTransport`（同源相对路径），仅当【开发模式（`import.meta.env.DEV`）+ `?mock=1`】双条件同时满足时才动态加载 `MockTransport` 做无后端演示；该分支被静态门控 tree-shake，故生产构建 / `web:preview` 中 `?mock=1` 无效且产物不含任何 mock 代码。CORS 与同源反代部署、启动脚本与 `GatewayClient` 最小用法详见 [Web 客户端文档](web-client.md)。

## 在线问答链路

```text
User
  -> CLI/Web Adapter
  -> Agent Runtime (packages/agent/core)
  -> OpenAI-compatible ModelProvider
  -> search_rules
  -> Retrieval API
  -> vector + BM25 child candidate search
  -> weighted reciprocal-rank fusion
  -> read_rules
  -> parent document store
  -> Citation Registry
  -> grounded final answer
```

`search_rules` 只返回候选摘要，降低上下文占用。`read_rules` 才返回完整父文档，并在应用侧注册 `[S1]`、`[S2]` 等稳定引用。最终来源展示来自 Citation Registry，不从模型输出中反向解析。

## 离线索引链路

```text
PF source files
  -> source adapter
  -> structural parser
  -> normalized parent documents
  -> child chunker
  -> BGE embeddings
  -> Chroma child index + parent document store
```

父文档保持完整章节结构，供 `read_rules` 返回；约 500 字的重叠子块只参与召回。当前默认使用 `BAAI/bge-small-zh-v1.5`，由 FastEmbed/ONNX 在 CPU 上生成归一化向量，Chroma 使用 cosine 距离。在线检索同时取得向量候选与 BM25 候选，通过加权 Reciprocal Rank Fusion 合并父文档排名，再应用轻量规则来源优先级。BM25 使用中文单字/双字词元和英文单词，并缓存语料 IDF；嵌入引擎与检索器通过接口隔离，不需要修改 Agent 工具协议。

索引构建会记录源文档 SHA-256、模型、嵌入引擎和切块参数。中断后按已有 Chroma 条目续建；配置不一致时拒绝继续，防止混入语义空间不同的向量。

## 检索质量闭环

`rulepacks/pathfinder-1e/evals/retrieval.jsonl` 保存问题与相关父文档 ID。评测命令直接调用真实检索器，报告 Hit@K 和 MRR，并保留逐题排名与路径。扩充规则或更换索引策略时，应先运行同一评测集再比较结果。

## 安全边界

- Agent 不能直接读取文件系统或数据库。
- 所有规则访问都必须经过有类型的工具。
- 每轮限制工具调用、搜索次数和完整文档数量。
- 规则集 ID 由运行时注入，模型不能跨库切换。
- 演示资料带有 `metadata.demo=true`，Prompt 强制披露其非正式性质。

## 后续领域工作流

规则问答稳定后，再增加结构化角色状态和确定性工具：

```text
get_character_options
apply_character_choice
calculate_character
validate_character
export_character
```

LLM 负责理解意图和解释选择；数值计算、前置条件和合法性判断由确定性代码负责。
