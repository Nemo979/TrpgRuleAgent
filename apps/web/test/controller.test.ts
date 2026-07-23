import { describe, expect, it, vi } from "vitest";
import { ChatController, type ChatClient } from "../src/controller.ts";
import type { Action } from "../src/state.ts";
import type { GatewayEvent, SessionCreateOptions } from "../src/types.ts";

interface FakeRec {
  runCalls: Array<{ input: string; apiKey: string }>;
  aborted: boolean;
  createOpts: SessionCreateOptions | null;
}

/** 构造一个 fake 客户端：runTurn 脚本化返回事件序列，记录所有调用。不访问真实网络。 */
function makeFakeClient(
  opts: { events?: GatewayEvent[]; throwOnRun?: boolean; throwAbortOnRun?: boolean } = {},
): {
  client: ChatClient;
  rec: FakeRec;
} {
  const rec: FakeRec = { runCalls: [], aborted: false, createOpts: null };

  const client: ChatClient = {
    async createSession(o) {
      rec.createOpts = o;
      // 与 SDK 一致：返回安全公开快照，不含 sessionToken。
      return { connected: true, turnInFlight: false, expiresAt: "2099-01-01T00:00:00.000Z" };
    },
    async *runTurn(input, options) {
      rec.runCalls.push({ input, apiKey: options.apiKey });
      if (opts.throwOnRun) {
        throw new Error("network down");
      }
      if (opts.throwAbortOnRun) {
        const err = new Error("已取消") as Error & { code: string };
        err.code = "aborted";
        throw err;
      }
      for (const e of opts.events ?? []) {
        yield e;
      }
    },
    abort() {
      rec.aborted = true;
    },
    disconnect() {
      rec.aborted = true;
    },
    async deleteSession() {},
    async reset() {},
  };

  return { client, rec };
}

const SCRIPT: GatewayEvent[] = [
  { type: "turn_start" },
  { type: "text_delta", delta: "借机" },
  { type: "text_delta", delta: "攻击" },
  { type: "tool_start", toolCallId: "t1", toolName: "search_rules" },
  { type: "tool_end", toolCallId: "t1", toolName: "search_rules" },
  { type: "turn_end" },
  { type: "sources", sources: [{ label: "S1", documentId: "d1", fullPath: "/p" }] },
  { type: "done" },
];

describe("ChatController", () => {
  it("sendTurn 按顺序派发事件（用户消息 → 各 Gateway 事件）", async () => {
    const { client } = makeFakeClient({ events: SCRIPT });
    const dispatched: Action[] = [];
    const controller = new ChatController(client, (a) => dispatched.push(a));

    await controller.sendTurn("什么时候触发借机攻击？", "sk-123");

    expect(dispatched[0]).toMatchObject({
      type: "ui/user_message",
      content: "什么时候触发借机攻击？",
    });
    const types = dispatched.map((d) => d.type);
    expect(types).toContain("event/turn_start");
    expect(types).toContain("event/text_delta");
    expect(types).toContain("event/tool_start");
    expect(types).toContain("event/tool_end");
    expect(types).toContain("event/turn_end");
    expect(types).toContain("event/sources");
    expect(types).toContain("event/done");
  });

  it("sendTurn 透传 input 与 apiKey 给 runTurn（apiKey 不保存在控制器）", async () => {
    const fake = makeFakeClient({
      events: [
        { type: "turn_start" },
        { type: "turn_end" },
        { type: "done" },
      ],
    });
    const dispatched: Action[] = [];
    const controller = new ChatController(fake.client, (a) => dispatched.push(a));
    await controller.sendTurn("hello", "secret-key");
    expect(dispatched.some((d) => d.type === "ui/user_message")).toBe(true);
    expect(fake.rec.runCalls[0]).toEqual({ input: "hello", apiKey: "secret-key" });
  });

  it("abort 透传到 client.abort()", () => {
    const fake = makeFakeClient();
    const controller = new ChatController(fake.client, () => {});
    controller.abort();
    expect(fake.rec.aborted).toBe(true);
  });

  it("createSession 透传参数并广播连接状态", async () => {
    const fake = makeFakeClient();
    const dispatched: Action[] = [];
    const controller = new ChatController(fake.client, (a) => dispatched.push(a));
    const opts: SessionCreateOptions = {
      provider: "openai-compatible",
      model: { id: "gpt-4o-mini" },
      baseUrl: "https://api.openai.com/v1",
      rulesetId: "pathfinder-1e",
    };
    await controller.createSession(opts);
    expect(fake.rec.createOpts).toEqual(opts);
    expect(dispatched).toContainEqual({ type: "connection/connecting" });
    expect(dispatched).toContainEqual({
      type: "connection/connected",
      expiresAt: "2099-01-01T00:00:00.000Z",
    });
  });

  it("runTurn 抛错时派发 connection/error（不访问真实网络）", async () => {
    const fake = makeFakeClient({ throwOnRun: true });
    const dispatched: Action[] = [];
    const controller = new ChatController(fake.client, (a) => dispatched.push(a));
    await controller.sendTurn("hi", "sk");
    expect(dispatched[dispatched.length - 1]).toMatchObject({ type: "connection/error" });
  });

  it("runTurn 抛 aborted 错误时派发 ui/turn_aborted，而不是 connection/error", async () => {
    const fake = makeFakeClient({ throwAbortOnRun: true });
    const dispatched: Action[] = [];
    const controller = new ChatController(fake.client, (a) => dispatched.push(a));
    await controller.sendTurn("hi", "sk");
    expect(dispatched[dispatched.length - 1]).toEqual({ type: "ui/turn_aborted" });
    expect(dispatched.some((d) => d.type === "connection/error")).toBe(false);
  });

  it("消费事件流途中 abort()：以 ui/turn_aborted 收尾，不显示连接异常", async () => {
    // fake runTurn：产出一个事件后抛 DOM 风格 AbortError（模拟 fetch 被取消）。
    const rec: { aborted: boolean } = { aborted: false };
    const client: ChatClient = {
      async createSession() {
        return { connected: true, turnInFlight: false };
      },
      async *runTurn() {
        yield { type: "turn_start" } as GatewayEvent;
        yield { type: "text_delta", delta: "部分" } as GatewayEvent;
        const err = new Error("The operation was aborted.");
        err.name = "AbortError";
        throw err;
      },
      abort() {
        rec.aborted = true;
      },
      disconnect() {},
      async deleteSession() {},
      async reset() {},
    };
    const dispatched: Action[] = [];
    const controller = new ChatController(client, (a) => dispatched.push(a));
    await controller.sendTurn("hi", "sk");
    expect(dispatched[dispatched.length - 1]).toEqual({ type: "ui/turn_aborted" });
    expect(dispatched.some((d) => d.type === "connection/error")).toBe(false);
  });

  it("newSession 调用 reset 并重置上下文", async () => {
    const reset = vi.fn().mockResolvedValue(undefined);
    const { client } = makeFakeClient();
    const clientWithReset: ChatClient = { ...client, reset };
    const dispatched: Action[] = [];
    const controller = new ChatController(clientWithReset, (a) => dispatched.push(a));
    await controller.newSession();
    expect(reset).toHaveBeenCalledOnce();
    expect(dispatched).toContainEqual({ type: "ui/new_session" });
  });
});
