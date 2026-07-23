/**
 * GatewayTransport：与 DOM/宿主尽量解耦的传输抽象。
 *
 * 只暴露两种能力：
 * - request：普通 JSON 请求/响应（创建会话、删除会话）；
 * - stream：POST + SSE 流式响应（runTurn），返回字节块的异步迭代。
 *
 * 之所以不用 EventSource：EventSource 只支持 GET、不支持自定义请求头
 * （需要 Authorization 与 X-Model-Api-Key），也不支持请求体。因此这里
 * 定义自有契约，BrowserTransport 用 fetch + ReadableStream 实现，
 * 后续可加 WeChatTransport（微信小程序 wx.request/分包）等，
 * 而 GatewayClient 完全不感知底层实现。
 */

export interface TransportRequest {
  method: "GET" | "POST" | "DELETE";
  /** 相对 baseUrl 的路径，如 "/v1/sessions"。 */
  path: string;
  headers?: Record<string, string>;
  /** 已序列化的请求体（JSON 字符串）。 */
  body?: string;
  signal?: AbortSignal;
}

export interface TransportResponse {
  status: number;
  /** 已读取的响应体文本（可能为空字符串）。 */
  text: string;
}

export interface TransportStreamResponse {
  status: number;
  /** 响应内容类型（用于判断是否 SSE）。 */
  contentType: string;
  /**
   * 响应体字节流。仅在 status 表示成功时才需要迭代；
   * 失败时调用方读取 errorText 即可。
   */
  body: AsyncIterable<Uint8Array>;
  /** 便捷读取错误响应体文本（非流式错误分支）。 */
  readText(): Promise<string>;
}

export interface GatewayTransport {
  /** 普通 JSON 请求。传输层失败应抛出 TransportError。 */
  request(req: TransportRequest): Promise<TransportResponse>;
  /** 流式请求（SSE）。传输层失败应抛出 TransportError。 */
  stream(req: TransportRequest): Promise<TransportStreamResponse>;
}

/**
 * 传输层错误：由具体 Transport 实现在网络/取消失败时抛出。
 * 只携带归一化分类，绝不携带请求头/URL/底层 cause，避免凭据外泄。
 * GatewayClient 会把它翻译成对外的 GatewayClientError。
 */
export class TransportError extends Error {
  readonly kind: "network" | "aborted";

  constructor(kind: "network" | "aborted", message: string) {
    super(message);
    this.name = "TransportError";
    this.kind = kind;
  }
}
