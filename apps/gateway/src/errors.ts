/**
 * Gateway 层错误：携带 HTTP 状态码与机器可读 code。
 * message 面向客户端，禁止包含凭据或内部堆栈信息。
 */
export class GatewayError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "GatewayError";
    this.status = status;
    this.code = code;
  }
}
