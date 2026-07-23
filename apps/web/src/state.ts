/**
 * 纯函数状态机（apps/web/src/state.ts）。
 *
 * 无任何 DOM / SDK 依赖，可单测。
 * reducer(state, action) -> state 必须是幂等、可预测的，便于在测试中逐事件验证。
 */
import type { GatewaySource, GatewayWireError } from "./types.ts";

export type ConnectionStatus = "disconnected" | "connecting" | "connected" | "error";

export interface UserMessage {
  role: "user";
  id: string;
  content: string;
}

export interface AssistantMessage {
  role: "assistant";
  id: string;
  content: string;
}

export type ChatMessage = UserMessage | AssistantMessage;

/** 工具状态时间线条目：只保存安全的 toolCallId / toolName / status，绝不保存 args / results。 */
export interface ToolTimelineEntry {
  toolCallId: string;
  toolName: string;
  status: "start" | "end" | "error";
  error?: GatewayWireError;
}

export interface ChatError {
  message: string;
  code?: string;
}

export interface ChatState {
  messages: ChatMessage[];
  /** 当前流式生成中的 assistant 文本（turn_end/error 后固化进 messages 并清空）。 */
  streamingText: string;
  /** assistant 消息自增序号，用于生成稳定 id（纯函数，状态内维护）。 */
  assistantSeq: number;
  toolTimeline: ToolTimelineEntry[];
  sources: GatewaySource[];
  error: ChatError | null;
  connection: ConnectionStatus;
  connected: boolean;
  generating: boolean;
  sessionExpiresAt?: string;
}

export type Action =
  // —— 连接状态（UI 动作）——
  | { type: "connection/connecting" }
  | { type: "connection/connected"; expiresAt?: string }
  | { type: "connection/disconnected" }
  | { type: "connection/error"; message: string; code?: string }
  // —— UI 动作 ——
  | { type: "ui/user_message"; id: string; content: string }
  | { type: "ui/new_session" }
  | { type: "ui/clear_error" }
  // 用户主动停止：固化已生成的部分文本，结束生成态；不算连接异常、不进错误区。
  | { type: "ui/turn_aborted" }
  // —— Gateway 事件（镜像 GatewayEvent）——
  | { type: "event/turn_start" }
  | { type: "event/text_delta"; delta: string }
  | { type: "event/tool_start"; toolCallId: string; toolName: string }
  | { type: "event/tool_end"; toolCallId: string; toolName: string }
  | { type: "event/tool_error"; toolCallId: string; toolName: string; error: GatewayWireError }
  | { type: "event/turn_end" }
  | { type: "event/sources"; sources: GatewaySource[] }
  | { type: "event/error"; error: GatewayWireError }
  | { type: "event/done" };

export function createInitialState(): ChatState {
  return {
    messages: [],
    streamingText: "",
    assistantSeq: 0,
    toolTimeline: [],
    sources: [],
    error: null,
    connection: "disconnected",
    connected: false,
    generating: false,
  };
}

/** 派生：是否允许发送消息（必须已连接且当前没有生成中的 turn）。 */
export function canSend(state: ChatState): boolean {
  return state.connected && !state.generating;
}

/** 派生：是否允许点击「创建会话」（连接中禁止重复提交；已连接允许重建）。 */
export function canCreateSession(state: ChatState): boolean {
  return state.connection !== "connecting";
}

/** 把当前 streamingText 固化为一条 assistant 消息（若非空），并清空流式缓冲。 */
function finalizeTurn(state: ChatState): ChatState {
  if (state.streamingText.length === 0) {
    return { ...state, streamingText: "" };
  }
  const message: AssistantMessage = {
    role: "assistant",
    id: `assistant-${state.assistantSeq}`,
    content: state.streamingText,
  };
  return {
    ...state,
    messages: [...state.messages, message],
    streamingText: "",
    assistantSeq: state.assistantSeq + 1,
  };
}

export function reducer(state: ChatState, action: Action): ChatState {
  switch (action.type) {
    case "connection/connecting":
      return { ...state, connection: "connecting", error: null };

    case "connection/connected":
      return {
        ...state,
        connection: "connected",
        connected: true,
        error: null,
        ...(action.expiresAt !== undefined ? { sessionExpiresAt: action.expiresAt } : {}),
      };

    case "connection/disconnected":
      return {
        ...state,
        connection: "disconnected",
        connected: false,
        generating: false,
        streamingText: "",
      };

    case "connection/error":
      return {
        ...state,
        connection: "error",
        generating: false,
        error: { message: action.message, ...(action.code !== undefined ? { code: action.code } : {}) },
      };

    case "ui/user_message": {
      const message: UserMessage = { role: "user", id: action.id, content: action.content };
      return { ...state, messages: [...state.messages, message] };
    }

    case "ui/new_session":
      // 断开 / 新会话：清空整个会话上下文，回到初始（保留 connection 为 disconnected）。
      return {
        ...createInitialState(),
        connection: "disconnected",
        connected: false,
      };

    case "ui/clear_error":
      return { ...state, error: null };

    case "ui/turn_aborted":
      // 用户主动取消：保留连接状态（仍是 connected），固化部分输出，
      // 不写入 error（取消不是连接异常）。
      return finalizeTurn({ ...state, generating: false });

    case "event/turn_start":
      // 开始新一轮：重置流式缓冲与工具时间线、清空上轮来源。
      return {
        ...state,
        generating: true,
        streamingText: "",
        toolTimeline: [],
        sources: [],
        error: null,
      };

    case "event/text_delta":
      return { ...state, streamingText: state.streamingText + action.delta };

    case "event/tool_start": {
      const entry: ToolTimelineEntry = {
        toolCallId: action.toolCallId,
        toolName: action.toolName,
        status: "start",
      };
      return { ...state, toolTimeline: [...state.toolTimeline, entry] };
    }

    case "event/tool_end": {
      const idx = state.toolTimeline.findIndex((e) => e.toolCallId === action.toolCallId);
      if (idx < 0) {
        return state;
      }
      const existing = state.toolTimeline[idx];
      if (!existing) {
        return state;
      }
      const next = state.toolTimeline.slice();
      next[idx] = { ...existing, status: "end" };
      return { ...state, toolTimeline: next };
    }

    case "event/tool_error": {
      const idx = state.toolTimeline.findIndex((e) => e.toolCallId === action.toolCallId);
      const next = state.toolTimeline.slice();
      if (idx < 0 || !next[idx]) {
        // 未记录到 tool_start 也补齐一条错误条目，保证时间线可见。
        const entry: ToolTimelineEntry = {
          toolCallId: action.toolCallId,
          toolName: action.toolName,
          status: "error",
          error: action.error,
        };
        next.push(entry);
      } else {
        next[idx] = { ...next[idx]!, status: "error", error: action.error };
      }
      return { ...state, toolTimeline: next };
    }

    case "event/turn_end":
      return finalizeTurn({ ...state, generating: false });

    case "event/sources":
      return { ...state, sources: action.sources };

    case "event/error":
      return finalizeTurn({
        ...state,
        generating: false,
        error: {
          message: action.error.message,
          ...(action.error.category !== undefined ? { code: action.error.category } : {}),
        },
      });

    case "event/done":
      // 终止帧：确保结束生成态（turn_end 通常已先行固化消息）。
      return { ...state, generating: false };

    default: {
      // 穷尽性检查：新加的 Action 分支若忘记处理会在此报错。
      const _exhaustive: never = action;
      return _exhaustive;
    }
  }
}
