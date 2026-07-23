/**
 * 内部消息模型。Agent Loop 与工具层只依赖这里的结构，
 * 不感知任何模型厂商的数据格式。
 */

export interface Usage {
  inputTokens?: number;
  outputTokens?: number;
  totalTokens?: number;
}

/** 助手消息中一次完整的工具调用。arguments 为原始 JSON 字符串。 */
export interface ToolCall {
  id: string;
  name: string;
  arguments: string;
}

export interface SystemMessage {
  role: "system";
  content: string;
}

export interface UserMessage {
  role: "user";
  content: string;
}

export interface AssistantMessage {
  role: "assistant";
  content: string;
  toolCalls: ToolCall[];
  usage?: Usage;
}

export interface ToolMessage {
  role: "tool";
  toolCallId: string;
  toolName: string;
  content: string;
  isError: boolean;
}

export type AgentMessage = SystemMessage | UserMessage | AssistantMessage | ToolMessage;
