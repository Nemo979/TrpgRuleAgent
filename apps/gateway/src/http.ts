import type { IncomingMessage, ServerResponse } from "node:http";
import { GatewayError } from "./errors.ts";

/** 所有响应统一附加的安全头：凭据相关内容绝不允许被缓存或泄露 referrer。 */
export function applySecurityHeaders(res: ServerResponse): void {
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("Referrer-Policy", "no-referrer");
  res.setHeader("X-Content-Type-Options", "nosniff");
}

/**
 * CORS 处理：仅回显白名单内的 Origin，绝不使用通配符。
 * 返回 true 表示该请求是已完成响应的 OPTIONS 预检。
 */
export function handleCors(
  req: IncomingMessage,
  res: ServerResponse,
  allowedOrigins: string[],
): boolean {
  const origin = req.headers.origin;
  if (typeof origin === "string" && allowedOrigins.includes(origin)) {
    res.setHeader("Access-Control-Allow-Origin", origin);
    res.setHeader("Vary", "Origin");
  }

  if (req.method === "OPTIONS") {
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Model-Api-Key");
    res.setHeader("Access-Control-Max-Age", "600");
    res.statusCode = 204;
    res.end();
    return true;
  }
  return false;
}

/**
 * 读取并解析 JSON 请求体：
 * - Content-Type 必须为 application/json；
 * - 字节数超过 maxBodyBytes 立即中止（413）；
 * - 非法 JSON 或非对象顶层结构返回 400。
 */
export async function readJsonBody(
  req: IncomingMessage,
  maxBodyBytes: number,
): Promise<Record<string, unknown>> {
  const contentType = req.headers["content-type"] ?? "";
  if (!contentType.toLowerCase().startsWith("application/json")) {
    throw new GatewayError(415, "unsupported_media_type", "Content-Type 必须是 application/json");
  }

  const chunks: Buffer[] = [];
  let total = 0;
  for await (const chunk of req) {
    const buffer = chunk as Buffer;
    total += buffer.length;
    if (total > maxBodyBytes) {
      throw new GatewayError(413, "payload_too_large", `请求体超过 ${maxBodyBytes} 字节上限`);
    }
    chunks.push(buffer);
  }

  const text = Buffer.concat(chunks).toString("utf8");
  if (text.trim() === "") {
    return {};
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new GatewayError(400, "invalid_json", "请求体不是合法 JSON");
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new GatewayError(400, "invalid_json", "请求体必须是 JSON 对象");
  }
  return parsed as Record<string, unknown>;
}

/** 统一 JSON 响应：{ data } 或 { error: { code, message } }。 */
export function sendJson(res: ServerResponse, status: number, payload: unknown): void {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.end(JSON.stringify(payload));
}

export function sendError(res: ServerResponse, error: GatewayError): void {
  sendJson(res, error.status, { error: { code: error.code, message: error.message } });
}
