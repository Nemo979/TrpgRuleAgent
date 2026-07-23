export type AgentErrorCategory =
  | "configuration_error"
  | "provider_http_error"
  | "provider_protocol_error"
  | "invalid_tool_call"
  | "unknown_tool"
  | "tool_execution_error"
  | "limit_exceeded"
  | "aborted";

export interface AgentErrorOptions {
  cause?: unknown;
  status?: number;
}

/**
 * 统一的 Agent 错误类型。
 *
 * 约定：message 与 cause 中不得包含 API Key 等敏感凭据；
 * 构造错误信息时只允许引用状态码、类别与服务端返回的文本片段。
 */
export class AgentError extends Error {
  readonly category: AgentErrorCategory;
  readonly status: number | undefined;

  constructor(category: AgentErrorCategory, message: string, options?: AgentErrorOptions) {
    super(message, options?.cause !== undefined ? { cause: options.cause } : undefined);
    this.name = "AgentError";
    this.category = category;
    this.status = options?.status;
  }
}

export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

export function describeError(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

/**
 * 把未知异常规整为 AgentError。fallback 用于既不是 AgentError
 * 也不是取消错误的场景。
 */
export function toAgentError(
  error: unknown,
  fallback: AgentErrorCategory = "provider_protocol_error",
): AgentError {
  if (error instanceof AgentError) {
    return error;
  }
  if (isAbortError(error)) {
    return new AgentError("aborted", "请求已被取消", { cause: error });
  }
  return new AgentError(fallback, describeError(error), { cause: error });
}

export function throwIfAborted(signal: AbortSignal | undefined): void {
  if (signal?.aborted) {
    throw new AgentError("aborted", "请求已被取消");
  }
}

export const REDACTED = "[REDACTED]";

/**
 * 在文本中把敏感值（如 API Key）全部替换为 [REDACTED]。
 * 安全优先：只有 secret 为空（undefined 或 ""）时才跳过；
 * 任何非空 API Key（包括长度 1、2、3）都必须被完整替换，
 * 即使可能误伤正常文本，也不得让密钥出现在错误信息里。
 */
export function redactSecret(text: string, secret: string | undefined): string {
  if (!secret) {
    return text;
  }
  return text.split(secret).join(REDACTED);
}

/**
 * 把任意 cause 安全化，确保整个可观察错误链都不含原始密钥。
 * - Error：只保留 name 与已脱敏 message，构造一个全新的 Error，
 *   丢弃原对象上可能残留密钥的 stack、嵌套 cause 或自定义字段。
 * - 非 Error：无法确定其内部是否安全，一律丢弃（返回 undefined）。
 */
function sanitizeCause(cause: unknown, secret: string | undefined): Error | undefined {
  if (!(cause instanceof Error)) {
    return undefined;
  }
  const safe = new Error(redactSecret(cause.message, secret));
  safe.name = cause.name;
  return safe;
}

/**
 * 返回一个整个错误链都已脱敏的 AgentError。
 * 顶层 message 与 cause.message 都会被脱敏；即使顶层 message
 * 未命中密钥，也必须安全化 cause，因为底层 Error.cause.message
 * 仍可能包含 API Key（打印错误链时会泄露）。
 * category 与 status 始终保留以维持诊断能力。
 */
export function redactAgentError(error: AgentError, secret: string | undefined): AgentError {
  const safeMessage = redactSecret(error.message, secret);
  const safeCause = sanitizeCause(error.cause, secret);
  return new AgentError(error.category, safeMessage, {
    ...(safeCause !== undefined ? { cause: safeCause } : {}),
    ...(error.status !== undefined ? { status: error.status } : {}),
  });
}
