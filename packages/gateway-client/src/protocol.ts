/**
 * BYOK Agent Gateway 公开线协议类型（docs/gateway-api.md）。
 *
 * 刻意不从 @trpg-rule-agent/agent 或 apps/gateway 导入：
 * 客户端 SDK 只依赖公开的 wire 协议，不依赖任何服务端内部类型，
 * 保证零运行时依赖、可独立打包进浏览器/小程序等任意宿主。
 */

/** SSE 事件中的错误载荷：只有 category 与 message，无 cause/stack。 */
export interface GatewayWireError {
  category: string;
  message: string;
}

/** sources 事件中的引用来源卡片。 */
export interface GatewaySource {
  label: string;
  documentId: string;
  fullPath: string;
  title?: string;
}

/**
 * POST /v1/turns 的 SSE 公开事件。
 * 与 Gateway 的 PublicEvent 一一对应；客户端解析时按显式白名单投影，
 * 未知字段一律丢弃，未知事件类型视为协议错误。
 */
export type GatewayEvent =
  | { type: "turn_start" }
  | { type: "text_delta"; delta: string }
  | { type: "tool_start"; toolCallId: string; toolName: string }
  | { type: "tool_end"; toolCallId: string; toolName: string }
  | { type: "tool_error"; toolCallId: string; toolName: string; error: GatewayWireError }
  | { type: "turn_end" }
  | { type: "error"; error: GatewayWireError }
  | { type: "sources"; sources: GatewaySource[] }
  | { type: "done" };

/** 创建会话时的模型描述（BYOK 自定义模型）。 */
export interface SessionModelConfig {
  id: string;
  contextWindow?: number;
  maxTokens?: number;
  reasoning?: boolean;
}

/** POST /v1/sessions 请求体。凭据字段绝不允许出现在这里。 */
export interface SessionCreateOptions {
  provider: string;
  model: SessionModelConfig;
  baseUrl: string;
  rulesetId?: string;
}

/**
 * POST /v1/sessions 成功响应的 data 部分（wire 类型）。
 * 仅描述线上的响应结构，供内部解析使用；GatewayClient.createSession
 * 的公开返回值是安全快照（GatewaySessionStatus），**不含 sessionToken**——
 * token 只写入客户端私有内存字段。
 */
export interface SessionCreateResponse {
  sessionToken: string;
  expiresAt: string;
}
