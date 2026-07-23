import { GatewayClientError, redactSecret } from "./errors.ts";
import type { GatewayEvent, GatewaySource, GatewayWireError } from "./protocol.ts";

/**
 * 零依赖 SSE 帧解析器（客户端侧）。
 *
 * 输入任意切分的 Uint8Array 字节流，产出每个事件的 data 载荷字符串：
 * - 行可以跨 chunk 到达，UTF-8 多字节字符可以跨 chunk（TextDecoder stream 模式）；
 * - 一个 chunk 可以包含多行、多个事件；
 * - 支持 \n 与 \r\n 行结束符；
 * - 同一事件的多个 data: 行按 SSE 规范用 \n 连接；
 * - 注释行（: 开头）与 event:/id:/retry: 字段被忽略；
 * - 流结束时残留的 data 行会被冲刷（兼容缺少结尾空行的服务端）。
 */
export async function* parseSseFrames(
  source: AsyncIterable<Uint8Array>,
): AsyncGenerator<string> {
  const decoder = new TextDecoder();
  let buffer = "";
  let dataLines: string[] = [];

  const takeEvent = (): string | undefined => {
    if (dataLines.length === 0) {
      return undefined;
    }
    const payload = dataLines.join("\n");
    dataLines = [];
    return payload;
  };

  const handleLine = (rawLine: string): string | undefined => {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (line === "") {
      return takeEvent();
    }
    if (line.startsWith(":")) {
      return undefined;
    }
    if (line.startsWith("data:")) {
      let value = line.slice(5);
      if (value.startsWith(" ")) {
        value = value.slice(1);
      }
      dataLines.push(value);
    }
    // 其他字段（event:/id:/retry:）当前无需处理。
    return undefined;
  };

  for await (const chunk of source) {
    buffer += decoder.decode(chunk, { stream: true });
    let newlineIndex: number;
    while ((newlineIndex = buffer.indexOf("\n")) !== -1) {
      const line = buffer.slice(0, newlineIndex);
      buffer = buffer.slice(newlineIndex + 1);
      const payload = handleLine(line);
      if (payload !== undefined) {
        yield payload;
      }
    }
  }

  buffer += decoder.decode();
  if (buffer.length > 0) {
    const payload = handleLine(buffer);
    if (payload !== undefined) {
      yield payload;
    }
  }
  const finalPayload = takeEvent();
  if (finalPayload !== undefined) {
    yield finalPayload;
  }
}

const KNOWN_EVENT_TYPES = new Set([
  "turn_start",
  "text_delta",
  "tool_start",
  "tool_end",
  "tool_error",
  "turn_end",
  "error",
  "sources",
  "done",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function requireString(record: Record<string, unknown>, field: string, secret?: string): string {
  const value = record[field];
  if (typeof value !== "string") {
    throw new GatewayClientError("protocol_error", `事件字段 ${field} 必须是字符串`, { secret });
  }
  return value;
}

function parseWireError(value: unknown, secret?: string): GatewayWireError {
  if (!isRecord(value)) {
    throw new GatewayClientError("protocol_error", "error 字段必须是对象", { secret });
  }
  const category = typeof value.category === "string" ? value.category : "unknown";
  const rawMessage = typeof value.message === "string" ? value.message : "";
  // 二次兜底脱敏：即便服务端遗漏，客户端也不会把 Key 交给宿主 UI。
  return { category, message: redactSecret(rawMessage, secret) };
}

function parseSources(value: unknown, secret?: string): GatewaySource[] {
  if (!Array.isArray(value)) {
    throw new GatewayClientError("protocol_error", "sources 字段必须是数组", { secret });
  }
  return value.map((item) => {
    if (!isRecord(item)) {
      throw new GatewayClientError("protocol_error", "source 必须是对象", { secret });
    }
    const source: GatewaySource = {
      label: requireString(item, "label", secret),
      documentId: requireString(item, "documentId", secret),
      fullPath: requireString(item, "fullPath", secret),
    };
    if (typeof item.title === "string") {
      source.title = item.title;
    }
    return source;
  });
}

/**
 * 将单个 SSE data 载荷解析并投影为 typed GatewayEvent。
 *
 * - 非法 JSON -> malformed_event（不含原始文本，避免回显潜在敏感数据）；
 * - 结构/类型不符协议 -> protocol_error；
 * - 未知事件类型 -> protocol_error（严格白名单，防止未来字段被无声吞掉）；
 * - error/tool_error 的 message 再兜底脱敏。
 */
export function decodeGatewayEvent(payload: string, secret?: string): GatewayEvent {
  let parsed: unknown;
  try {
    parsed = JSON.parse(payload);
  } catch {
    throw new GatewayClientError("malformed_event", "SSE 事件不是合法 JSON", { secret });
  }
  if (!isRecord(parsed)) {
    throw new GatewayClientError("protocol_error", "SSE 事件必须是 JSON 对象", { secret });
  }
  const type = parsed.type;
  if (typeof type !== "string" || !KNOWN_EVENT_TYPES.has(type)) {
    throw new GatewayClientError("protocol_error", `未知事件类型：${String(type)}`, { secret });
  }

  switch (type) {
    case "turn_start":
      return { type: "turn_start" };
    case "text_delta":
      return { type: "text_delta", delta: requireString(parsed, "delta", secret) };
    case "tool_start":
      return {
        type: "tool_start",
        toolCallId: requireString(parsed, "toolCallId", secret),
        toolName: requireString(parsed, "toolName", secret),
      };
    case "tool_end":
      return {
        type: "tool_end",
        toolCallId: requireString(parsed, "toolCallId", secret),
        toolName: requireString(parsed, "toolName", secret),
      };
    case "tool_error":
      return {
        type: "tool_error",
        toolCallId: requireString(parsed, "toolCallId", secret),
        toolName: requireString(parsed, "toolName", secret),
        error: parseWireError(parsed.error, secret),
      };
    case "turn_end":
      return { type: "turn_end" };
    case "error":
      return { type: "error", error: parseWireError(parsed.error, secret) };
    case "sources":
      return { type: "sources", sources: parseSources(parsed.sources, secret) };
    case "done":
      return { type: "done" };
    default:
      // 已被上面的白名单拦截，这里仅为穷尽类型。
      throw new GatewayClientError("protocol_error", `未知事件类型：${type}`, { secret });
  }
}
