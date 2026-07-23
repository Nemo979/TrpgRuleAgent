# Web 客户端：共享 Gateway 客户端 SDK 与调试 UI

阶段三在 BYOK Agent Gateway（见 [Gateway API](gateway-api.md)）之上新增了两个可复用产物：

- `packages/gateway-client`：零运行时依赖的共享客户端 SDK，封装 Gateway 的 HTTP/SSE 协议、typed 事件与错误处理；
- `apps/web`：基于 Vite 的浏览器调试 UI（vanilla TypeScript + CSS），产物为纯静态文件，用于本地联调与无后端演示。

两者的共同目标是：让浏览器/小程序等宿主能够安全地消费 Gateway，且 API Key 永不离开宿主内存。

## 概述

### packages/gateway-client（共享 SDK）

- **零运行时依赖**：只使用宿主环境原生的 `fetch`、`ReadableStream`、`AbortController`、Web Streams 等标准能力，不引入任何第三方包。因此可被直接打包进浏览器脚本、Vite 产物或未来的小程序运行时。
- **协议层与 SDK 实现解耦**：SDK 只依赖 Gateway 的公开 wire 协议（见 `docs/gateway-api.md`），不导入 `apps/gateway` 或 `packages/agent` 的任何内部类型。
- **GatewayTransport 抽象**：一个与 DOM/宿主尽量解耦的传输接口（`request` + `stream`）。具体传输实现可替换，GatewayClient 完全不感知底层。
- **BrowserTransport**：基于原生 `fetch` + `ReadableStream` 的浏览器实现。
- **WeChatTransport（可选）**：面向微信小程序宿主的传输实现；微信能力（`wx.request` 等）由宿主封装成最小适配接口注入，SDK 不依赖 `wx` 全局对象（见下文“微信小程序接入”）。

为什么不使用 `EventSource`？

- `EventSource` 只支持 `GET`，不支持请求体；
- `EventSource` 不支持自定义请求头，而 Gateway 的 `/v1/turns` 需要 `Authorization`（会话 token）与 `X-Model-Api-Key`（模型 Key）两个自定义头；
- Gateway 的 turn 是 `POST` 并携带 `input` 请求体，随后以 SSE 流返回。

因此 SDK 定义自有契约，`BrowserTransport` 用 `fetch` 的 `body.getReader()` 逐块产出字节，天然支持 `AbortSignal` 取消，并在 `fetch` 上设置 `redirect: "error"` 拒绝自动跟随重定向（防 SSRF / 凭据被重定向到非预期端点）。

### apps/web（调试 UI）

- **Vite + vanilla TypeScript/CSS**：无前端框架，状态、控制器与视图分离（见 [架构文档](architecture.md) 阶段三分层）。
- **Vite 仅作为 devDependency**：开发用 `vite` dev server，构建产出 `apps/web/dist` 下的一组纯静态文件（HTML/CSS/JS），可托管到任意静态服务器或同源反向代理后。
- **同源默认**：`BrowserTransport` 的 `baseUrl` 默认空串，表示走相对路径、与宿主同源。生产推荐通过反向代理让静态资源与 `/v1/*` 在同一源暴露，从而无需 CORS、也避免凭据跨域。

## SDK API 速览

### GatewayClient

构造：`new GatewayClient({ transport })`，注入一个 `GatewayTransport` 实现（浏览器用 `BrowserTransport`）。

| 方法 | 说明 |
| --- | --- |
| `createSession(options: SessionCreateOptions): Promise<GatewaySessionStatus>` | `POST /v1/sessions`，返回的 `sessionToken` **只写入客户端私有内存字段**；公开返回值是安全快照（`connected`/`expiresAt`/`turnInFlight`），**不含 token**。`options` 只含 `provider`/`model`/`baseUrl`/`rulesetId`，**绝不允许出现任何凭据字段**。 |
| `runTurn(input: string, options: RunTurnOptions): AsyncGenerator<GatewayEvent>` | `POST /v1/turns`，`for await` 流式产出 typed 事件。`options.apiKey` 仅经 `X-Model-Api-Key` 头传递，函数返回后不留引用；`options.signal` 与外部取消合并。需先 `createSession`，否则抛 `invalid_state`；同一实例已有进行中的 turn 时抛 `turn_in_flight`。成功响应必须是 `text/event-stream`，且 `done` 必须为最后一帧；Content-Type 不符或流在 `done` 之前提前结束均抛 `protocol_error`。 |
| `abort(): void` | 取消进行中的 turn（幂等）。 |
| `deleteSession(): Promise<void>` | `DELETE /v1/session`，best-effort：网络失败或 404 都视为已断开，只清理本地状态。会先 `abort` 当前 turn。 |
| `disconnect(): void` | 本地断开：清空内存会话状态，不发网络请求。用于“刷新即丢失”语义或用户主动断开。 |
| `reset(): Promise<void>` | 语义化别名，等价于 `deleteSession`，便于 UI 的“新会话”按钮调用。 |
| `status(): GatewaySessionStatus` | 公开状态快照，**刻意不含 `sessionToken` 或任何凭据**，可安全 `JSON.stringify` 用于 UI 调试展示。 |

### GatewayEvent（typed 事件）

与 Gateway 的 SSE 公开事件一一对应：

```text
turn_start
text_delta { delta: string }
tool_start { toolCallId, toolName }
tool_end   { toolCallId, toolName }
tool_error { toolCallId, toolName, error: { category, message } }
turn_end
error      { error: { category, message } }
sources    { sources: { label, documentId, fullPath, title? }[] }
done
```

客户端解析时按显式白名单投影，未知字段丢弃，未知事件类型视为协议错误。

### GatewayClientError（typed 错误）

统一错误类 `GatewayClientError`，带 `code` 字段（`GatewayClientErrorCode`）。安全不变量：绝不在任何字段中保存 API Key 或 session token；`message` 在构造时基于 `secret` 兜底脱敏；不保留底层 `cause` 对象（可能含请求头/URL/凭据）。可用 `isGatewayClientError(value)` 做类型守卫。

`code` 取值：

| code | 含义 |
| --- | --- |
| `network_error` | 传输层失败（DNS/断网/CORS 被拒等），不含底层细节 |
| `aborted` | 请求被 `abort()` / 断开取消 |
| `http_error` | Gateway 返回非 2xx；具体见 `gatewayCode` / `status` |
| `malformed_event` | SSE 数据帧不是合法 JSON |
| `protocol_error` | SSE 事件结构不符合协议（缺字段、未知类型、字段类型错误） |
| `unauthorized` | 会话未认证/已过期（HTTP 401 或缺少 token 的本地防护） |
| `turn_in_flight` | 同一客户端实例已有进行中的 turn（本地并发防护） |
| `stream_error` | SSE 流内出现 `error` 事件（Agent/Gateway 侧终止性错误） |
| `invalid_state` | 调用方用法错误（如未创建会话即 `runTurn`） |

## 最小使用示例

```ts
import {
  BrowserTransport,
  GatewayClient,
} from "@trpg-rule-agent/gateway-client";

// baseUrl 留空表示同源相对路径（生产推荐同源反代）。
const transport = new BrowserTransport({ baseUrl: "" });
const client = new GatewayClient({ transport });

// 1) 创建会话（BYOK 连接配置，不含任何 Key）。
await client.createSession({
  provider: "openai-compatible",
  model: { id: "gpt-4o-mini" },
  baseUrl: "https://api.openai.com/v1",
  rulesetId: "pathfinder-1e",
});

// 2) 逐轮问答：模型 Key 只在本次调用经请求头传入。
const apiKey = prompt("模型 API Key"); // 实际应来自输入框，仅留内存
for await (const event of client.runTurn("什么时候会触发借机攻击？", { apiKey })) {
  if (event.type === "text_delta") {
    process.stdout.write(event.delta);
  } else if (event.type === "done") {
    break;
  }
}

// 3) 断开：清空内存会话状态，不发网络请求。
client.disconnect();
```

> 该示例所用 `apiKey` 应在宿主 UI 中仅以内存变量持有（见下节安全模型）。

## 安全模型

SDK 与 Web UI 共同遵循以下不变量（与 [Gateway API](gateway-api.md) 的 BYOK 模型一致）：

- **API Key 与 session token 只存在于实例/页面内存**：`GatewayClient` 的 token 保存在私有 `#session` 字段（`#private`，运行时不可枚举/反射），`runTurn` 的 `apiKey` 只作为局部变量经请求头传递，函数返回后不留引用。
- **刷新即丢失**：页面刷新会销毁 JS 运行时，内存中的 Key/token 随之消失。**刷新后必须重新填写 API Key 并重新创建会话**。这是 BYOK 的刻意设计，不是缺陷——服务端不持久化任何模型 Key 或 token 原始值。
- **严禁落盘**：绝不写入 `localStorage` / `sessionStorage` / `IndexedDB` / `cookie`，也不进入 DOM 元素的 `dataset`。
- **Key 不泄漏到可观察面**：Key 不进 URL（包括 query 与 hash）、不进 DOM `dataset`、不进日志、不进错误对象、不进 `status()` 快照。SDK 在多处基于 `secret` 对 `message` 做兜底脱敏（替换为 `[REDACTED]`），即使上游遗漏也不会把 Key 交给宿主 UI。
- **公开 `status()` 不含 token**：`GatewaySessionStatus` 只暴露 `connected` / `expiresAt` / `turnInFlight`，供 UI 渲染与调试展示，可安全 `JSON.stringify`。
- **模型输出只用 `textContent` 渲染**：Web UI 对 `text_delta` 与来源等模型输出一律通过 `textContent` 写入 DOM，禁用 `innerHTML` 与任何 Markdown/HTML 注入，避免模型可控内容造成 XSS。
- **`fetch` 使用 `redirect: "error"`**：禁止自动跟随 30x，防止允许端点把带凭据的请求重定向到内网地址绕过 Base URL 白名单（SSRF）。

### 关于刷新后必须重新填写 API Key

请特别注意：**本系统刻意不持久化任何凭据**。API Key 与 session token 仅驻留于浏览器内存变量，一旦刷新页面即被浏览器回收。因此每次刷新后，用户都需要：

1. 重新填写模型 API Key；
2. 重新创建会话（旧的 `sessionToken` 已随刷新丢失，无法恢复）。

请勿自行把 Key 写入 `localStorage` 或任何持久化存储来“绕过”这一行为——那会违背 BYOK 的零服务端持久化安全边界。

## CORS 与部署

### 开发期：显式 CORS 白名单

Vite dev server 默认运行在 `http://localhost:5173`，而 Gateway 是另一个源。此时需要把前端源加入 Gateway 的 `GATEWAY_ALLOWED_ORIGINS` 白名单，Gateway 才会回显该 `Origin`：

```dotenv
GATEWAY_ALLOWED_ORIGINS=http://localhost:5173
```

`GATEWAY_ALLOWED_ORIGINS` 为空时不允许任何跨域来源，且 CORS 响应绝不使用 `*`。

### 生产：推荐同源反向代理

生产环境**推荐同源反向代理**：把 Web 静态产物与 Gateway 的 `/v1/*`、`/health` 通过同一域名暴露。这样：

- Web 静态资源与 API 处于同源，`BrowserTransport` 的 `baseUrl` 用空串（`""`）即可走相对路径；
- 无需 CORS，也避免凭据跨域；
- `fetch` 侧 `credentials: "omit"`，不误带 cookie。

nginx 反代示例（静态产物目录为 `/srv/trpg-web`，Gateway 监听 `127.0.0.1:8787`）：

```nginx
server {
    listen 80;
    server_name trpg.example.com;

    # 静态资源（apps/web/dist）
    location / {
        root /srv/trpg-web;
        try_files $uri $uri/ /index.html;
    }

    # Gateway 接口：同源反代，无需 CORS
    location /v1/ {
        proxy_pass http://127.0.0.1:8787;
        proxy_http_version 1.1;
        # SSE 需要关闭缓冲并拉长读超时，否则流会被攒批/提前断开
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location = /health {
        proxy_pass http://127.0.0.1:8787;
        proxy_http_version 1.1;
    }
}
```

> 注意：`/v1/` 与 `/health` 的 `proxy_pass` 必须带后端地址；SSE 流对应的 `/v1/` 需 `proxy_buffering off;` 且 `proxy_read_timeout` 足够长，否则事件会被缓冲或连接被过早关闭。

## 启动方式

根 package.json 提供以下脚本（由主 agent 添加，文档直接使用其命令名）：

```bash
# 开发服务器（默认 http://localhost:5173）
npm run web:dev

# 构建静态产物到 apps/web/dist
npm run web:build

# 本地预览构建产物
npm run web:preview
```

等价地，也可在子目录执行：`cd apps/web && npm run dev|build|preview`。

### ?mock=1 无后端演示（仅开发模式）

默认访问走**真实** `BrowserTransport`（同源相对路径，需要后端 Gateway）。

仅当【开发服务器（`npm run web:dev`）+ URL 带 `?mock=1`】两个条件同时满足时，Web UI 才动态加载内置 `MockTransport`（脚本化返回一段 SSE），用于没有后端时的端到端演示：

```
http://localhost:5173/?mock=1
```

> mock 分支以 `import.meta.env.DEV` 静态门控：生产构建时该条件被替换为 `false`，整个分支（含动态 `import("./mock-transport.ts")`）被 tree-shake 移除，**生产 bundle 不含任何 mock 代码 / token / fixture**（可用 `grep -r "MockTransport\|mock-transport" apps/web/dist/` 验证为空）。生产环境访问 `?mock=1` 无效，永远走真实路径。

## 微信小程序接入（WeChatTransport）

SDK 内置可选的 `WeChatTransport`（`packages/gateway-client/src/wechat-transport.ts`），
让微信小程序宿主复用同一套 `GatewayClient` / SSE 协议 / typed 事件 / 错误分类 / 脱敏逻辑。

### 边界与不变量

- **SDK 不依赖 `wx` 全局对象，也不引入微信 SDK / 任何运行时第三方依赖**。微信能力由宿主封装成两个最小适配器注入：
  - `request: WeChatRequestAdapter` —— 普通 JSON 请求（宿主用 `wx.request` 封装，回调式 `success/fail`，返回可 `abort` 的任务对象）；
  - `streamRequest: WeChatStreamAdapter` —— 分块流式请求（宿主用 `wx.request({ enableChunked: true })` + `RequestTask.onHeadersReceived/onChunkReceived` 封装，回调 `onHeaders/onChunkReceived/onComplete/onError`）。
- `WeChatTransport` 把回调式分块推送适配为与 `BrowserTransport` 相同的 `AsyncIterable<Uint8Array>`，SSE 解析（`parseSseFrames`）、事件白名单投影、`done` 终止判定全部复用，不假设 Node 或浏览器专有 API。
- **契约与 BrowserTransport 一致**：`AbortSignal` 取消 → `TransportError("aborted")`；其余传输层失败 → `TransportError("network")`；错误只携带归一化分类与固定文案，**绝不携带 URL、请求头、API Key 或底层 cause**（宿主的 `fail/onError` 错误对象会被丢弃而非透传）。
- **API Key 仍只由 `GatewayClient.runTurn` 每次调用传入**，仅经 `X-Model-Api-Key` 请求头发出；`WeChatTransport` 实例不保存任何凭据，也不触碰小程序 Storage。
- `baseUrl` **必填且必须是绝对 http(s) URL**（小程序没有“同源相对路径”概念），域名需在小程序后台配置为 request 合法域名。

### 宿主接入示意

```ts
import { GatewayClient, WeChatTransport } from "@trpg-rule-agent/gateway-client";

// 以下封装位于未来的 apps/miniprogram（宿主侧），SDK 本身不包含任何 wx 调用。
const transport = new WeChatTransport({
  baseUrl: "https://gateway.example.com",
  request: (p) =>
    wx.request({
      url: p.url, method: p.method, header: p.header, data: p.data,
      dataType: "其他", responseType: "text",   // 关闭自动 JSON 解析，返回纯文本
      success: (res) => p.success({ statusCode: res.statusCode, data: res.data }),
      fail: p.fail,
    }),
  streamRequest: (p) => {
    const task = wx.request({
      url: p.url, method: p.method, header: p.header, data: p.data,
      enableChunked: true,
      success: () => p.onComplete(), fail: p.onError,
    });
    task.onHeadersReceived((res) => p.onHeaders({ statusCode: res.statusCode, header: res.header }));
    task.onChunkReceived((res) => p.onChunkReceived(res.data));
    return task;
  },
});

const client = new GatewayClient({ transport }); // 之后的用法与浏览器完全相同
```

> 注意：部分基础库版本的 `onHeadersReceived` 不含 `statusCode`，宿主适配层需自行兜底，并保证“先 `onHeaders` 后首个 chunk”的调用顺序。本仓库刻意**不包含**真正的小程序工程（`apps/miniprogram` 属于未来工作），SDK 侧只交付传输层与契约测试。

## 未来扩展

`GatewayTransport` 是与 DOM 解耦的抽象，因此接入新的宿主只需新增一个传输实现，而 `GatewayClient` 与协议层完全复用：

- **微信小程序**：传输层 `WeChatTransport` 已就绪（见上节），剩余工作只是小程序视图层与 `wx.request` 适配器封装（`apps/miniprogram`）。
- **其他宿主**：任何提供 `fetch` 语义或可分块读取响应的环境，都可实现 `GatewayTransport` 接入。

这一分层保证 Agent Core 与 Gateway 的“零第三方运行时依赖”约束保持不变：SDK 自身同样零运行时依赖，新宿主只贡献一个薄传输层，不引入运行时耦合。
