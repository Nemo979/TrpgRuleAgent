import { describe, expect, it } from "vitest";
import {
  canCreateSession,
  canSend,
  createInitialState,
  reducer,
  type ChatState,
} from "../src/state.ts";

describe("state reducer", () => {
  it("text_delta 累加进流式缓冲", () => {
    let s: ChatState = createInitialState();
    s = reducer(s, { type: "event/turn_start" });
    s = reducer(s, { type: "event/text_delta", delta: "你好" });
    s = reducer(s, { type: "event/text_delta", delta: "世界" });
    expect(s.streamingText).toBe("你好世界");
  });

  it("turn_end 把流式文本固化为 assistant 消息并清空缓冲", () => {
    let s = createInitialState();
    s = reducer(s, { type: "event/turn_start" });
    s = reducer(s, { type: "event/text_delta", delta: "ABC" });
    s = reducer(s, { type: "event/turn_end" });
    expect(s.messages).toHaveLength(1);
    expect(s.messages[0]).toMatchObject({ role: "assistant", content: "ABC" });
    expect(s.streamingText).toBe("");
    expect(s.generating).toBe(false);
  });

  it("tool_start/tool_end/tool_error 生成并更新时间线", () => {
    let s = createInitialState();
    s = reducer(s, { type: "event/tool_start", toolCallId: "t1", toolName: "search_rules" });
    expect(s.toolTimeline[0]).toMatchObject({ toolName: "search_rules", status: "start" });

    s = reducer(s, { type: "event/tool_end", toolCallId: "t1", toolName: "search_rules" });
    expect(s.toolTimeline[0]?.status).toBe("end");

    s = reducer(s, {
      type: "event/tool_error",
      toolCallId: "t2",
      toolName: "retrieve",
      error: { category: "retrieval", message: "失败" },
    });
    const t2 = s.toolTimeline.find((e) => e.toolCallId === "t2");
    expect(t2?.status).toBe("error");
    expect(t2?.error?.category).toBe("retrieval");
  });

  it("sources 事件写入来源", () => {
    let s = createInitialState();
    s = reducer(s, {
      type: "event/sources",
      sources: [{ label: "S1", documentId: "doc-1", fullPath: "/a/b.md", title: "标题" }],
    });
    expect(s.sources).toHaveLength(1);
    expect(s.sources[0]?.documentId).toBe("doc-1");
  });

  it("error 事件写入错误并结束生成（不展示 args/results）", () => {
    let s = createInitialState();
    s = reducer(s, { type: "event/turn_start" });
    s = reducer(s, { type: "event/text_delta", delta: "部分" });
    s = reducer(s, { type: "event/error", error: { category: "auth", message: "鉴权失败" } });
    expect(s.error).toEqual({ message: "鉴权失败", code: "auth" });
    expect(s.generating).toBe(false);
  });

  it("开始新 turn 重置流式缓冲、工具时间线与上轮来源", () => {
    let s = createInitialState();
    s = reducer(s, { type: "event/turn_start" });
    s = reducer(s, { type: "event/text_delta", delta: "未完成" });
    s = reducer(s, { type: "event/tool_start", toolCallId: "t1", toolName: "x" });
    s = reducer(s, {
      type: "event/sources",
      sources: [{ label: "S1", documentId: "d1", fullPath: "/a.md" }],
    });
    // 新一轮开始
    s = reducer(s, { type: "event/turn_start" });
    expect(s.streamingText).toBe("");
    expect(s.toolTimeline).toHaveLength(0);
    expect(s.sources).toHaveLength(0);
    expect(s.generating).toBe(true);
  });

  it("ui/turn_aborted 固化部分输出、结束生成，且不写入 error、不改连接状态", () => {
    let s = createInitialState();
    s = reducer(s, { type: "connection/connected" });
    s = reducer(s, { type: "event/turn_start" });
    s = reducer(s, { type: "event/text_delta", delta: "生成到一半" });
    s = reducer(s, { type: "ui/turn_aborted" });
    expect(s.generating).toBe(false);
    expect(s.error).toBeNull();
    expect(s.connection).toBe("connected");
    expect(s.connected).toBe(true);
    expect(s.messages[s.messages.length - 1]).toMatchObject({
      role: "assistant",
      content: "生成到一半",
    });
    expect(s.streamingText).toBe("");
  });

  it("canSend：未连接或生成中禁止发送", () => {
    let s = createInitialState();
    expect(canSend(s)).toBe(false); // 未连接
    s = reducer(s, { type: "connection/connected" });
    expect(canSend(s)).toBe(true);
    s = reducer(s, { type: "event/turn_start" });
    expect(canSend(s)).toBe(false); // 生成中
    s = reducer(s, { type: "event/turn_end" });
    expect(canSend(s)).toBe(true);
  });

  it("canCreateSession：连接中禁止重复创建，其余状态允许", () => {
    let s = createInitialState();
    expect(canCreateSession(s)).toBe(true);
    s = reducer(s, { type: "connection/connecting" });
    expect(canCreateSession(s)).toBe(false);
    s = reducer(s, { type: "connection/connected" });
    expect(canCreateSession(s)).toBe(true);
    s = reducer(s, { type: "connection/error", message: "x" });
    expect(canCreateSession(s)).toBe(true);
  });

  it("连接状态：connected 置 connected=true，disconnected 复位", () => {
    let s = createInitialState();
    s = reducer(s, { type: "connection/connected", expiresAt: "2099-01-01T00:00:00.000Z" });
    expect(s.connected).toBe(true);
    expect(s.sessionExpiresAt).toBe("2099-01-01T00:00:00.000Z");
    s = reducer(s, { type: "connection/disconnected" });
    expect(s.connected).toBe(false);
  });

  it("ui/new_session 清空整个会话上下文", () => {
    let s = createInitialState();
    s = reducer(s, { type: "ui/user_message", id: "u1", content: "hi" });
    s = reducer(s, { type: "event/turn_start" });
    s = reducer(s, { type: "event/text_delta", delta: "yo" });
    s = reducer(s, { type: "ui/new_session" });
    expect(s.messages).toHaveLength(0);
    expect(s.streamingText).toBe("");
    expect(s.toolTimeline).toHaveLength(0);
    expect(s.connected).toBe(false);
  });
});
