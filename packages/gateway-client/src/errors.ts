/** 脱敏占位符，与 Gateway/Agent 侧约定一致。 */
export const REDACTED = "[REDACTED]";

/**
 * 将文本中出现的 secret 全部替换为 REDACTED。
 * secret 为空/未提供时原样返回。与服务端一样不设最短长度：
 * 即便是 2 字符的 Key 也会被替换（宁可误伤可读性，不可泄露凭据）。
 */
export function redactSecret(text: string, secret?: string): string {
  if (!secret) {
    return text;
  }
  return text.split(secret).join(REDACTED);
}

/** 客户端 typed error 分类。 */
export type GatewayClientErrorCode =
  /** 传输层失败（DNS/断网/CORS 被拒等），不含底层细节。 */
  | "network_error"
  /** 请求被 abort()/断开取消。 */
  | "aborted"
  /** Gateway 返回非 2xx；具体见 gatewayCode/status。 */
  | "http_error"
  /** SSE 数据帧不是合法 JSON。 */
  | "malformed_event"
  /** SSE 事件结构不符合协议（缺字段、未知类型、字段类型错误）。 */
  | "protocol_error"
  /** 会话未认证/已过期（HTTP 401 或缺少 token 的本地防护）。 */
  | "unauthorized"
  /** 同一客户端实例已有进行中的 turn（本地并发防护）。 */
  | "turn_in_flight"
  /** SSE 流内出现 error 事件（Agent/Gateway 侧终止性错误）。 */
  | "stream_error"
  /** 调用方用法错误（如未创建会话即 runTurn）。 */
  | "invalid_state";

export interface GatewayClientErrorOptions {
  /** Gateway JSON 错误体中的 code（若有）。 */
  gatewayCode?: string | undefined;
  /** HTTP 状态码（若来自 HTTP 响应）。 */
  status?: number | undefined;
  /** SSE error 事件的 category（若来自流内错误）。 */
  category?: string | undefined;
  /**
   * 本次请求用到的 API Key：仅用于在构造 message 时兜底脱敏，
   * 绝不作为字段保存到 error 上，也不进入序列化输出。
   */
  secret?: string | undefined;
}

/**
 * 客户端统一 typed error。
 *
 * 安全不变量：
 * - 绝不在任何字段（message/code/gatewayCode/category）中保存 API Key 或 session token；
 * - message 在构造时基于 secret 兜底脱敏，即使上游泄露也不会外传；
 * - 不保留底层 cause 对象（可能含请求头/URL/凭据），只保留归一化的分类信息。
 */
export class GatewayClientError extends Error {
  readonly code: GatewayClientErrorCode;
  readonly gatewayCode?: string;
  readonly status?: number;
  readonly category?: string;

  constructor(code: GatewayClientErrorCode, message: string, options: GatewayClientErrorOptions = {}) {
    super(redactSecret(message, options.secret));
    this.name = "GatewayClientError";
    this.code = code;
    if (options.gatewayCode !== undefined) {
      this.gatewayCode = options.gatewayCode;
    }
    if (options.status !== undefined) {
      this.status = options.status;
    }
    if (options.category !== undefined) {
      this.category = options.category;
    }
  }
}

/** 类型守卫：便于宿主 UI 精确分类处理。 */
export function isGatewayClientError(value: unknown): value is GatewayClientError {
  return value instanceof GatewayClientError;
}
