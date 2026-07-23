import type { IncomingMessage } from "node:http";
import { GatewayError } from "./errors.ts";

/**
 * 凭据边界工具。核心不变量：
 * - 模型 API Key 只允许出现在 X-Model-Api-Key 请求头中；
 * - 任何出现在 URL query 或 JSON body 中的疑似凭据字段都必须拒绝（400），
 *   防止 Key 落入访问日志、会话存储或错误信息。
 */

export const MODEL_API_KEY_HEADER = "x-model-api-key";

/** 疑似凭据字段名（大小写、下划线/连字符不敏感）。 */
const CREDENTIAL_FIELD_PATTERN = /^(api[-_]?key|apikey|key|token|secret|password|authorization|credentials?|bearer)$/i;

export function isCredentialFieldName(name: string): boolean {
  return CREDENTIAL_FIELD_PATTERN.test(name.trim());
}

/** 递归扫描 JSON 对象，发现疑似凭据字段即抛出 400。 */
export function assertNoCredentialFields(value: unknown, path = ""): void {
  if (Array.isArray(value)) {
    value.forEach((item, index) => assertNoCredentialFields(item, `${path}[${index}]`));
    return;
  }
  if (value === null || typeof value !== "object") {
    return;
  }
  for (const [key, child] of Object.entries(value)) {
    const childPath = path === "" ? key : `${path}.${key}`;
    if (isCredentialFieldName(key)) {
      throw new GatewayError(
        400,
        "credentials_not_allowed",
        `请求中不允许携带凭据字段（${childPath}）；模型 API Key 只能通过 X-Model-Api-Key 请求头传递`,
      );
    }
    assertNoCredentialFields(child, childPath);
  }
}

/** 拒绝 query string 中的疑似凭据参数，避免 Key 进入访问日志。 */
export function assertNoCredentialQuery(url: URL): void {
  for (const key of url.searchParams.keys()) {
    if (isCredentialFieldName(key)) {
      throw new GatewayError(
        400,
        "credentials_not_allowed",
        "URL query 中不允许携带凭据参数；模型 API Key 只能通过 X-Model-Api-Key 请求头传递",
      );
    }
  }
}

/** 从请求头提取模型 API Key。缺失时抛出 400。 */
export function extractModelApiKey(req: IncomingMessage): string {
  const raw = req.headers[MODEL_API_KEY_HEADER];
  const value = Array.isArray(raw) ? raw[0] : raw;
  if (!value || value.trim() === "") {
    throw new GatewayError(400, "missing_model_api_key", "缺少 X-Model-Api-Key 请求头");
  }
  return value.trim();
}

/** 从 Authorization: Bearer <token> 提取会话 token。缺失/格式错误抛出 401。 */
export function extractSessionToken(req: IncomingMessage): string {
  const raw = req.headers.authorization;
  if (!raw || !raw.startsWith("Bearer ")) {
    throw new GatewayError(401, "unauthorized", "缺少 Authorization: Bearer <sessionToken>");
  }
  const token = raw.slice("Bearer ".length).trim();
  if (token === "") {
    throw new GatewayError(401, "unauthorized", "会话 token 为空");
  }
  return token;
}
