/**
 * @trpg-rule-agent/miniprogram
 *
 * 微信小程序宿主接入骨架（非完整可发布工程）：
 * - wx-host.ts        宿主适配边界：把 wx.request 封装为 SDK 适配器；
 * - session-facade.ts 页面级会话门面：连接 / 发送 / 停止 / 新会话 / 卸载清理。
 *
 * 真实小程序工程由开发者在微信开发者工具中创建，把本目录源码复制进去
 * 并注入 `{ request: (o) => wx.request(o) }`，见 apps/miniprogram/README.md。
 */

export {
  createWeChatAdapters,
  type WeChatAdapters,
  type WxHost,
  type WxHostRequestOptions,
  type WxHostRequestTask,
} from "./wx-host.ts";

export {
  MiniProgramSession,
  type MiniChatClient,
  type MiniChatError,
  type MiniChatMessage,
  type MiniChatState,
  type MiniProgramSessionOptions,
  type MiniSessionConfig,
  type MiniToolEntry,
} from "./session-facade.ts";
