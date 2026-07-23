/**
 * @trpg-rule-agent/gateway-client
 *
 * 面向浏览器/小程序等宿主的 BYOK Agent Gateway 客户端 SDK。
 * 零运行时依赖；凭据只存在于实例内存，刷新即丢失。
 */

export type {
  GatewayEvent,
  GatewaySource,
  GatewayWireError,
  SessionCreateOptions,
  SessionCreateResponse,
  SessionModelConfig,
} from "./protocol.ts";

export {
  GatewayClientError,
  isGatewayClientError,
  redactSecret,
  REDACTED,
  type GatewayClientErrorCode,
  type GatewayClientErrorOptions,
} from "./errors.ts";

export { parseSseFrames, decodeGatewayEvent } from "./sse.ts";

export {
  TransportError,
  type GatewayTransport,
  type TransportRequest,
  type TransportResponse,
  type TransportStreamResponse,
} from "./transport.ts";

export { BrowserTransport, type BrowserTransportOptions } from "./browser-transport.ts";

export {
  GatewayClient,
  type GatewayClientOptions,
  type GatewaySessionStatus,
  type RunTurnOptions,
} from "./client.ts";
