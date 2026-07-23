# BYOK Agent Gateway API

`apps/gateway` 是面向浏览器前端的 HTTP/SSE 网关。BYOK（Bring Your Own Key）凭据模型：

- 模型 API Key 由前端页面内存持有（刷新即丢失），服务端**不加载、不存储、不缓存**任何模型 Key；
- Key 只允许通过每次 turn 请求的 `X-Model-Api-Key` 头传入，生命周期等于该次请求；
- **模型连接配置由用户自带**：创建会话时提交 `provider`/`model`/`baseUrl`（BYOK 自定义模型），每个会话用自己的配置创建 Agent，服务端不设全局模型；
- 会话存储只保存非敏感连接配置、消息历史与过期时间，token 以 SHA-256 摘要落表，原始 token 只在创建时返回一次；
- `retrievalBaseUrl` 始终由服务端注入，客户端不可覆盖；
- 出现在 URL query 或 JSON body 中的疑似凭据字段（`apiKey`、`token`、`secret` 等）一律 400 拒绝，防止 Key 进入访问日志或会话存储。

仅使用 Node.js 22 内置模块（`http`、`crypto`、`url`）与 workspace 包，无第三方运行时依赖。

## 启动

```bash
npm run gateway
```

### 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `GATEWAY_HOST` | `127.0.0.1` | 监听地址 |
| `GATEWAY_PORT` | `8787` | 监听端口 |
| `GATEWAY_SESSION_TTL_MS` | `1800000`（30 分钟） | 会话空闲 TTL，每次访问续期 |
| `GATEWAY_RATE_LIMIT_MAX_REQUESTS` | `120` | 单进程、单来源限流窗口内最大请求数；健康检查和 OPTIONS 不计入 |
| `GATEWAY_RATE_LIMIT_WINDOW_MS` | `60000` | 单进程来源限流窗口（毫秒） |
| `RETRIEVAL_BASE_URL` | `http://127.0.0.1:8765` | 规则检索服务地址；可替换为云端向量/混合检索服务的 HTTPS 地址 |
| `GATEWAY_ALLOWED_ORIGINS` | 空 | CORS Origin 白名单，逗号分隔；为空则不允许任何跨域来源 |
| `GATEWAY_MODEL_BASE_URL_ALLOWLIST` | 空 | 模型 Base URL **前缀**白名单，逗号分隔（如 `https://api.openai.com/v1`）。按 origin + 路径边界匹配：`/v1` 命中 `/v1`、`/v1/chat`，不命中 `/v1x` 或 `/admin`。条目本身必须为 HTTPS、不含 userinfo/query/fragment |
| `GATEWAY_ALLOW_LOCALHOST_MODEL` | `false` | 本地开发开关：允许 `http://localhost` / `127.0.0.1` 模型端点 |
| `GATEWAY_ALLOWED_RULESETS` | 空（即只允许默认规则集） | 允许客户端选择的规则集，逗号分隔；默认规则集必须在其中 |
| `RULESET_ID` | `pathfinder-1e` | `rulesetId` 缺省时使用的默认规则集 |
| `RETRIEVAL_BASE_URL` | `http://127.0.0.1:8765` | 检索服务地址，**始终由服务端注入**，客户端不可覆盖 |

`LLM_MODEL`/`LLM_BASE_URL`/`LLM_API_KEY` 对 Gateway 一律**无效**：模型连接配置由客户端在创建会话时提交，Key 必须由客户端每次请求携带。

## 通用约定

- 所有响应带 `Cache-Control: no-store`、`Referrer-Policy: no-referrer`、`X-Content-Type-Options: nosniff`；
- JSON 响应统一为 `{ "data": ... }` 或 `{ "error": { "code", "message" } }`；
- 请求体上限 16 KiB，`Content-Type` 必须为 `application/json`（无体的请求除外）；
- CORS 只回显白名单内的 Origin，绝不使用 `*`。

## 接口

### GET /health

```json
{ "data": { "status": "ok" } }
```

### POST /v1/sessions

创建会话并绑定本会话的模型连接配置（BYOK 自定义模型）。请求体：

```json
{
  "provider": "openai-compatible",
  "model": {
    "id": "gpt-4o-mini",
    "contextWindow": 128000,
    "maxTokens": 8192,
    "reasoning": false
  },
  "baseUrl": "https://api.openai.com/v1",
  "rulesetId": "pathfinder-1e"
}
```

校验规则（全部违规返回 400）：

- `provider`（必填）：必须已在 Provider Registry 注册；
- `model`（必填对象）：仅允许 `id`（必填非空字符串）、`contextWindow`/`maxTokens`（可选正整数）、`reasoning`（可选布尔）；
- `baseUrl`（必填）：还需通过模型端点白名单校验（不通过返回 `403 model_base_url_forbidden`）；
- `rulesetId`（可选）：缺省用服务端默认值；不在允许集合内返回 `400 ruleset_not_allowed`；
- **拒绝**：空 body（无法确定模型）、未知字段（顶层与 `model` 内部）、客户端提交 `retrievalBaseUrl`、任何凭据字段（含嵌套，`400 credentials_not_allowed`）。

成功响应：

```json
{
  "data": {
    "sessionToken": "P-Zy…（base64url，43+ 字符，仅此一次返回）",
    "expiresAt": "2026-07-23T12:00:00.000Z"
  }
}
```

### POST /v1/turns

执行一轮问答，返回 SSE 流。Agent 使用**该会话创建时绑定的** provider/model/baseUrl/rulesetId；API Key 只来自本次请求的 `X-Model-Api-Key` 头，用完即弃。

请求头：

```
Authorization: Bearer <sessionToken>
X-Model-Api-Key: <模型 API Key>
Content-Type: application/json
```

请求体（只允许 `input`，最长 8000 字符）：

```json
{ "input": "什么时候会触发借机攻击？" }
```

错误：

| 状态 | code | 场景 |
| --- | --- | --- |
| 401 | `unauthorized` | 缺失/无效/过期的会话 token |
| 400 | `missing_model_api_key` | 缺少 `X-Model-Api-Key` |
| 400 | `credentials_not_allowed` | body/query 中出现凭据字段 |
| 400 | `invalid_request` / `input_too_long` | 未知字段、input 非法或超长 |
| 403 | `model_base_url_forbidden` | 模型端点不在白名单 |
| 409 | `turn_in_flight` | 同一会话已有进行中的 turn |
| 413 / 415 | `payload_too_large` / `unsupported_media_type` | 体积或类型限制 |

SSE 事件（`data: <json>\n\n`，字段经显式白名单投影，绝不透出内部对象）：

```text
{"type":"turn_start"}
{"type":"text_delta","delta":"…"}
{"type":"tool_start","toolCallId":"…","toolName":"search_rules"}
{"type":"tool_end","toolCallId":"…","toolName":"search_rules"}
{"type":"tool_error","toolCallId":"…","toolName":"…","error":{"category":"…","message":"…"}}
{"type":"turn_end"}
{"type":"sources","sources":[{"label":"S1","documentId":"prd-combat-aoo","fullPath":"…","title":"…"}]}
{"type":"error","error":{"category":"…","message":"…"}}
{"type":"done"}
```

约定：

- `error`（终止性）与 `turn_end` 互斥；`sources` 仅在成功 turn 后发出；`done` 恒为最后一帧；
- 错误只含 `category` 与 `message`，无 `cause`/`stack`；`message` 不含 API Key 由**两层**保证：内置 Provider 会脱敏，且 Gateway SSE 出口会基于本次 `X-Model-Api-Key` 对 `error`/`tool_error` 的 `message` **再兜底脱敏一次**（替换为 `[REDACTED]`），不依赖任何自定义 Provider 的自觉；
- 客户端断开连接会取消本轮（AbortSignal），失败/取消的 turn 由 Runtime 回滚，不污染会话历史；只有成功 turn 的消息会写回会话。

### DELETE /v1/session

删除 Bearer token 指定的会话。成功 `204`；不存在或已过期 `404 session_not_found`。

## 边界与扩展点

- **SessionStore** 为全异步接口（所有方法返回 `Promise`），默认 `InMemorySessionStore`（单实例）。快照包含非敏感连接配置、消息、创建/过期时间与版本号；未来可实现 Redis/加密存储以支持多实例与多租户，接口层面已保证不接收任何凭据。进行中的 turn 不会被空闲 TTL 静默删除，`beginTurn`/`saveMessages`/`endTurn` 均会续期。
- **ModelEndpointPolicy** 为可注入契约，默认 `AllowlistModelEndpointPolicy`：候选 URL 必须 HTTPS、无 userinfo/query/fragment，并按 origin + 路径边界匹配前缀白名单（`/v1` 不会放行 `/v1x` 或 `/admin`）；白名单条目在构造时自校验。`GATEWAY_ALLOW_LOCALHOST_MODEL=true` 时放行本机端点（仅限开发）。
- **禁止自动重定向**：模型请求使用 `fetch(..., { redirect: "error" })`。即便初始 `baseUrl` 命中白名单，允许端点仍可能以 30x 把请求重定向到内网地址绕过 URL 策略（SSRF）；因此当前实现**不跟随任何重定向**，收到 30x 直接失败并归一为 `provider_http_error`（错误信息同样经统一脱敏）。
- **AgentFactory** 依赖注入：签名为 `(sessionConfig, initialMessages) => Agent`，按每个会话自己的连接配置创建 Agent（不捕获全局模型配置）；生产工厂只补充服务端的 `retrievalBaseUrl`。测试可注入 fake agent。
