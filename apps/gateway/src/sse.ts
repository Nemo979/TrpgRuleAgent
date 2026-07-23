import type { ServerResponse } from "node:http";
import { redactSecret, type AgentEvent } from "@trpg-rule-agent/agent";

/**
 * 对外 SSE 公共事件。与内部 AgentEvent 解耦：
 * 通过显式字段投影（白名单）序列化，绝不直接 JSON.stringify
 * 内部事件对象，防止 cause/stack/工具原始结果等内部信息外泄。
 */
export type PublicEvent =
  | { type: "turn_start" }
  | { type: "text_delta"; delta: string }
  | { type: "tool_start"; toolCallId: string; toolName: string }
  | { type: "tool_end"; toolCallId: string; toolName: string }
  | { type: "tool_error"; toolCallId: string; toolName: string; error: PublicError }
  | { type: "turn_end" }
  | { type: "error"; error: PublicError }
  | { type: "sources"; sources: PublicSource[] }
  | { type: "done" };

export interface PublicError {
  category: string;
  message: string;
}

export interface PublicSource {
  label: string;
  documentId: string;
  fullPath: string;
  title?: string;
}

/**
 * 内部 AgentEvent -> 公共事件投影。
 * - 错误只保留 category 与 message，cause、stack、status 等一律丢弃；
 * - message 在 Gateway 末端再基于本次 X-Model-Api-Key 做一次精确脱敏：
 *   ModelProvider 是可扩展接口，自定义 Provider 未必调用 redactAgentError，
 *   其 error/tool_error 的 message 可能直接包含本次 apiKey；这里不依赖
 *   Provider 的自觉，出口处兜底替换为 [REDACTED]；
 * - 工具事件只保留 id 与名称，参数与原始结果不透出。
 */
export function toPublicEvent(event: AgentEvent, secret?: string): PublicEvent {
  switch (event.type) {
    case "turn_start":
      return { type: "turn_start" };
    case "text_delta":
      return { type: "text_delta", delta: event.delta };
    case "tool_start":
      return { type: "tool_start", toolCallId: event.toolCallId, toolName: event.toolName };
    case "tool_end":
      return { type: "tool_end", toolCallId: event.toolCallId, toolName: event.toolName };
    case "tool_error":
      return {
        type: "tool_error",
        toolCallId: event.toolCallId,
        toolName: event.toolName,
        error: { category: event.error.category, message: redactSecret(event.error.message, secret) },
      };
    case "turn_end":
      return { type: "turn_end" };
    case "error":
      return {
        type: "error",
        error: { category: event.error.category, message: redactSecret(event.error.message, secret) },
      };
  }
}

/** SSE 输出器：负责响应头与 data 帧编码。 */
export class SseWriter {
  private readonly res: ServerResponse;

  constructor(res: ServerResponse) {
    this.res = res;
  }

  start(): void {
    this.res.statusCode = 200;
    this.res.setHeader("Content-Type", "text/event-stream; charset=utf-8");
    this.res.setHeader("Cache-Control", "no-store");
    this.res.setHeader("Connection", "keep-alive");
    this.res.flushHeaders?.();
  }

  send(event: PublicEvent): void {
    if (this.res.writableEnded || this.res.destroyed) {
      return;
    }
    try {
      this.res.write(`data: ${JSON.stringify(event)}\n\n`);
    } catch {
      // 客户端已断开：静默丢弃，交由 close 处理器取消本轮。
    }
  }

  end(): void {
    if (!this.res.writableEnded && !this.res.destroyed) {
      this.res.end();
    }
  }
}
