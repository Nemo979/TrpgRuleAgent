import type { AgentError } from "./errors.ts";
import type { ToolResult } from "./tools.ts";

/**
 * Agent Runtime 对外的稳定事件流。CLI、未来的 Web/小程序适配层
 * 都只消费这些事件。
 */
export type AgentEvent =
  | { type: "turn_start" }
  | { type: "text_delta"; delta: string }
  | { type: "tool_start"; toolCallId: string; toolName: string; arguments: unknown }
  | { type: "tool_end"; toolCallId: string; toolName: string; result: ToolResult }
  | { type: "tool_error"; toolCallId: string; toolName: string; error: AgentError }
  | { type: "turn_end" }
  | { type: "error"; error: AgentError };
