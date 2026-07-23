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
  server.ts / index.ts  路由与进程入口
```

协议与限制详见 [Gateway API](gateway-api.md)。核心不变量：模型 Key 的生命周期等于一次 turn 请求；模型连接配置（provider/model/baseUrl/rulesetId）由用户创建会话时提交、逐会话生效，`retrievalBaseUrl` 始终由服务端注入；SessionStore 不存在凭据字段；失败 turn 不污染会话历史。

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
