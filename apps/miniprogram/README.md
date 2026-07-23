# apps/miniprogram — 微信小程序宿主接入骨架

> **这不是一个完整可发布的小程序工程。**
> 这里只提供「宿主接入示例 / 适配边界」：可复制的纯 TypeScript 源码 + 纯 Node 单元测试。
> 真实小程序工程（`app.json`、页面 WXML/WXSS、项目配置）由开发者在**微信开发者工具**中自行创建，
> 再把 `src/` 下的文件复制进去并绑定 `wx` 适配器。

## 分层与边界

```
Page（开发者在微信开发者工具中创建，绑定 setData）
  └─ MiniProgramSession（src/session-facade.ts）
       连接配置 / API Key 内存态 / 创建会话 / 发送 turn /
       流式事件累加 / 停止生成 / 新会话 / 页面卸载清理
       └─ GatewayClient（packages/gateway-client，协议与 SSE 解析完全复用）
            └─ WeChatTransport（packages/gateway-client，不依赖 wx 全局）
                 └─ createWeChatAdapters(host)（src/wx-host.ts，宿主适配边界）
                      └─ host = { request: (o) => wx.request(o) }   ← 唯一接触 wx 的一行，
                                                                       由开发者在小程序里粘贴
```

- 核心 SDK（`packages/gateway-client`）与本骨架的 `src/` **都不引用 `wx` 全局对象**，
  也不安装微信 SDK / `miniprogram-api-typings`——`wx-host.ts` 里的 `WxHost` 系列接口
  只是对 `wx.request` 形状的最小结构化声明；
- 宿主 `fail/onError` 的错误对象（可能含 `errMsg`/URL 细节）在适配边界被丢弃，
  SDK 只会看到固定文案的占位错误，最终归一化为 `TransportError("network" | "aborted")`；
- 未来若要替换为真实 wx 宿主实现或其他宿主（如 uni-app），只需替换注入的 `host` 对象。

## 安全不变量

- **API Key 仅存页面内存**（`MiniProgramSession` 的 `#apiKey` 私有字段）：
  绝不写入 `wx.setStorage*`、URL、日志或任何持久化介质；
- 小程序**切后台（onHide）不做任何持久化**；页面**卸载（onUnload）必须调用 `destroy()`**
  清空内存凭据与会话——重新进入页面需要重新粘贴 Key（与 Web 端「刷新即丢失」同语义）；
- session token 由 `GatewayClient` 的 `#private` 字段持有，门面与页面均不可见；
- 状态快照（`MiniChatState`）绝不包含凭据，可安全交给 `setData`；
- 错误文案基于内存中的 API Key 兜底脱敏（`redactSecret`）。

## 部署前提

1. **HTTPS**：Gateway 必须以 HTTPS 提供服务（小程序正式环境强制 HTTPS；
   开发者工具中可临时勾选“不校验合法域名”联调 http://localhost）；
2. **request 合法域名**：在[微信公众平台](https://mp.weixin.qq.com) →
   开发管理 → 开发设置 → 服务器域名，把 Gateway 域名加入 **request 合法域名**；
   BYOK 模型端点由 Gateway 服务端转发，**无需**配置模型厂商域名；
3. 基础库需支持 `wx.request` 的 `enableChunked` + `RequestTask.onChunkReceived`
   （基础库 ≥ 2.20.1；部分低版本 `onHeadersReceived` 不含 `statusCode`，
   适配层已做兜底，见 `src/wx-host.ts`）。

## 页面绑定示例（复制到微信开发者工具）

把 `src/wx-host.ts`、`src/session-facade.ts` 复制进小程序工程（TS 模板可直接用；
JS 模板请先经 `tsc` 编译或手动去类型），页面 JS 大致如下：

```js
// pages/chat/index.js
import { MiniProgramSession } from "../../lib/session-facade";

Page({
  data: { chat: null, input: "", apiKeyInput: "" },

  onLoad() {
    this.session = new MiniProgramSession({
      // 唯一接触 wx 的一行：把 wx.request 作为宿主能力注入。
      host: { request: (options) => wx.request(options) },
      onState: (state) => this.setData({ chat: state }),
    });
  },

  async onConnectTap() {
    const ok = await this.session.connect(
      {
        gatewayBaseUrl: "https://gateway.example.com",   // 需配置为 request 合法域名
        provider: "openai",
        model: { id: "gpt-4o-mini" },
        modelBaseUrl: "https://api.openai.com/v1",       // 需在 Gateway 端点白名单内
        // rulesetId: "pathfinder-1e",                   // 可选
      },
      this.data.apiKeyInput,                             // 仅进入内存，不落盘
    );
    if (ok) {
      this.setData({ apiKeyInput: "" });                 // 成功后立即清空输入框
    }
  },

  async onSendTap() {
    const input = this.data.input;
    this.setData({ input: "" });
    await this.session.send(input);
  },

  onStopTap() {
    this.session.stop();
  },

  async onNewSessionTap() {
    await this.session.newSession();                     // 同时清空内存中的 API Key
  },

  onUnload() {
    this.session.destroy();                              // 必须：清空内存凭据与会话
  },
});
```

WXML 侧把 `chat.messages` / `chat.streamingText` / `chat.generating` /
`chat.error` 绑定到列表与按钮禁用态即可（`chat` 内没有任何凭据字段）。

## 测试

本目录自带纯 Node/Vitest 单元测试（不需要微信环境）：

```bash
npx vitest run apps/miniprogram
```

覆盖：API Key 生命周期、重复建会话清理旧会话、发送/停止、新会话、
卸载清理、异常脱敏、宿主适配器不向 SDK 泄漏 wx 错误，以及
「src 不引用 wx 全局 / 不触碰 Storage」的静态安全断言。
