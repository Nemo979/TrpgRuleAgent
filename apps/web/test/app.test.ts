import { describe, expect, it } from "vitest";
import { App } from "../src/app.ts";
import type { ChatClient } from "../src/controller.ts";
import type { Action } from "../src/state.ts";
import type { GatewayEvent } from "../src/types.ts";

interface FakeClientRec {
  aborted: number;
  deleted: number;
  resetCalls: number;
  runApiKeys: string[];
}

function makeClient(rec: FakeClientRec, opts: { failCreate?: boolean } = {}): ChatClient {
  return {
    async createSession() {
      if (opts.failCreate) {
        throw new Error("create failed");
      }
      return { connected: true, turnInFlight: false };
    },
    async *runTurn(_input: string, options: { apiKey: string }): AsyncIterable<GatewayEvent> {
      rec.runApiKeys.push(options.apiKey);
      yield { type: "turn_start" };
      yield { type: "turn_end" };
      yield { type: "done" };
    },
    abort() {
      rec.aborted += 1;
    },
    disconnect() {},
    async deleteSession() {
      rec.deleted += 1;
    },
    async reset() {
      rec.resetCalls += 1;
      rec.deleted += 1;
    },
  };
}

function newRec(): FakeClientRec {
  return { aborted: 0, deleted: 0, resetCalls: 0, runApiKeys: [] };
}

const OPTIONS = { provider: "openai-compatible", model: { id: "m" }, baseUrl: "https://x/v1" };

describe("App（凭据内存 + 生命周期）", () => {
  it("createSession 成功后凭据在内存；sendTurn 使用该凭据", async () => {
    const rec = newRec();
    const dispatched: Action[] = [];
    const app = new App({
      createClient: async () => makeClient(rec),
      dispatch: (a) => dispatched.push(a),
    });

    const ok = await app.createSession("", "sk-secret", OPTIONS);
    expect(ok).toBe(true);
    expect(app.hasApiKeyInMemory).toBe(true);

    await app.sendTurn("你好");
    expect(rec.runApiKeys).toEqual(["sk-secret"]);

    // 即使 key 已进入内存并被使用，序列化 App 实例也绝不泄露它。
    expect(JSON.stringify(app)).not.toContain("sk-secret");
  });

  it("newSession（新会话/断开）清空内存凭据，并 best-effort 通知服务端删除", async () => {
    const rec = newRec();
    const dispatched: Action[] = [];
    const app = new App({
      createClient: async () => makeClient(rec),
      dispatch: (a) => dispatched.push(a),
    });
    await app.createSession("", "sk-secret", OPTIONS);
    expect(app.hasApiKeyInMemory).toBe(true);
    await app.sendTurn("第一条"); // 断开前发过一条，key 被使用一次

    await app.newSession();
    expect(app.hasApiKeyInMemory).toBe(false);
    expect(rec.resetCalls).toBe(1); // controller.newSession → client.reset（best-effort 删除）
    expect(dispatched).toContainEqual({ type: "ui/new_session" });

    // 断开后再发送：无 controller，凭据已清空，静默不发（不会用旧 key）。
    await app.sendTurn("hi");
    expect(rec.runApiKeys).toEqual(["sk-secret"]); // 没有新增
  });

  it("重复创建会话前 best-effort 清理旧 client（abort + deleteSession）", async () => {
    const rec1 = newRec();
    const rec2 = newRec();
    let calls = 0;
    const dispatched: Action[] = [];
    const app = new App({
      createClient: async () => {
        calls += 1;
        return calls === 1 ? makeClient(rec1) : makeClient(rec2);
      },
      dispatch: (a) => dispatched.push(a),
    });

    await app.createSession("", "sk-old", OPTIONS);
    await app.createSession("", "sk-new", OPTIONS);

    // 旧 client 被 best-effort 清理。
    expect(rec1.aborted).toBeGreaterThanOrEqual(1);
    expect(rec1.deleted).toBe(1);

    // 新会话使用新凭据。
    await app.sendTurn("hi");
    expect(rec2.runApiKeys).toEqual(["sk-new"]);
    expect(rec1.runApiKeys).toEqual([]);
  });

  it("createSession 失败返回 false，凭据保留以便重试，错误已广播", async () => {
    const rec = newRec();
    const dispatched: Action[] = [];
    const app = new App({
      createClient: async () => makeClient(rec, { failCreate: true }),
      dispatch: (a) => dispatched.push(a),
    });
    const ok = await app.createSession("", "sk-retry", OPTIONS);
    expect(ok).toBe(false);
    expect(app.hasApiKeyInMemory).toBe(true); // 保留，便于用户直接重试
    expect(dispatched.some((a) => a.type === "connection/error")).toBe(true);
  });

  it("client 工厂本身抛错：返回 false 并广播 connection/error（消息不含凭据）", async () => {
    const dispatched: Action[] = [];
    const app = new App({
      createClient: async () => {
        throw new Error("factory boom");
      },
      dispatch: (a) => dispatched.push(a),
    });
    const ok = await app.createSession("", "sk-secret", OPTIONS);
    expect(ok).toBe(false);
    const err = dispatched.find((a) => a.type === "connection/error");
    expect(err).toBeDefined();
    expect(JSON.stringify(err)).not.toContain("sk-secret");
  });

  it("公开状态 / 序列化 / 键枚举均不泄露 API Key", async () => {
    const rec = newRec();
    const dispatched: Action[] = [];
    const app = new App({
      createClient: async () => makeClient(rec),
      dispatch: (a) => dispatched.push(a),
    });
    const SECRET = "sk-super-secret-abc123";
    await app.createSession("", SECRET, OPTIONS);
    await app.sendTurn("触发一轮，确保 key 曾进入过内存路径");

    // 1) 公开只暴露布尔标志，绝不返回 key 本身。
    expect(app.hasApiKeyInMemory).toBe(true);
    expect(typeof app.hasApiKeyInMemory).toBe("boolean");

    // 2) JSON 序列化 App 实例：#private 字段不可枚举，不出现在结果里。
    expect(JSON.stringify(app)).not.toContain(SECRET);

    // 3) 自有可枚举键 + 深层遍历都不含 key。
    expect(Object.keys(app)).toEqual([]);
    const seen = new Set<unknown>();
    const walk = (v: unknown): void => {
      if (v === null || typeof v !== "object" || seen.has(v)) {
        return;
      }
      seen.add(v);
      for (const val of Object.values(v as Record<string, unknown>)) {
        if (typeof val === "string") {
          expect(val).not.toContain(SECRET);
        } else {
          walk(val);
        }
      }
    };
    walk(app);

    // 4) 派发出去的所有 Action（会进 reducer/UI）都不含 key。
    expect(JSON.stringify(dispatched)).not.toContain(SECRET);
  });

  it("newSession 后公开状态标志复位，且不残留 key", async () => {
    const rec = newRec();
    const app = new App({
      createClient: async () => makeClient(rec),
      dispatch: () => {},
    });
    const SECRET = "sk-to-be-cleared";
    await app.createSession("", SECRET, OPTIONS);
    await app.newSession();
    expect(app.hasApiKeyInMemory).toBe(false);
    expect(JSON.stringify(app)).not.toContain(SECRET);
  });
});
