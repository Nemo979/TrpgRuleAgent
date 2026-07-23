import { describe, expect, it } from "vitest";
import type { SessionConnectionConfig } from "../src/session-config.ts";
import { InMemorySessionStore } from "../src/session-store.ts";

/** 可控时钟。 */
function fakeClock(start = 1_000_000) {
  let now = start;
  return {
    now: () => now,
    advance(ms: number) {
      now += ms;
    },
  };
}

function connection(): SessionConnectionConfig {
  return {
    provider: "openai-compatible",
    model: { id: "test-model", maxTokens: 4096 },
    baseUrl: "https://llm.example/v1",
    rulesetId: "pathfinder-1e",
  };
}

describe("InMemorySessionStore", () => {
  it("create 返回高熵 token，内部只保存 SHA-256 摘要", async () => {
    const store = new InMemorySessionStore();
    const { sessionToken } = await store.create(connection());

    // base64url(32 bytes) = 43 字符。
    expect(sessionToken.length).toBeGreaterThanOrEqual(43);
    const keys = store.keys();
    expect(keys).toHaveLength(1);
    // 内部键是 64 位十六进制摘要，绝不等于/包含原始 token。
    expect(keys[0]).toMatch(/^[0-9a-f]{64}$/);
    expect(keys[0]).not.toBe(sessionToken);
    expect(keys[0]?.includes(sessionToken)).toBe(false);
  });

  it("快照包含非敏感连接配置、时间与 version，且无任何凭据字段", async () => {
    const clock = fakeClock();
    const store = new InMemorySessionStore({ ttlMs: 1000, now: clock.now });
    const { sessionToken } = await store.create(connection());
    const snapshot = await store.touch(sessionToken);

    expect(snapshot).toBeDefined();
    expect(Object.keys(snapshot!).sort()).toEqual([
      "config", "createdAt", "expiresAt", "lastAccessAt", "messages", "version",
    ]);
    expect(snapshot!.config).toEqual(connection());
    expect(snapshot!.createdAt).toBe(1_000_000);
    expect(snapshot!.lastAccessAt).toBe(1_000_000);
    expect(snapshot!.expiresAt).toBe(1_001_000);
    expect(snapshot!.version).toBe(0);

    const serialized = JSON.stringify(snapshot).toLowerCase();
    expect(serialized).not.toContain("apikey");
    expect(serialized).not.toContain("api_key");
    expect(serialized).not.toContain("secret");
  });

  it("touch 续期空闲 TTL；过期后访问返回 undefined 并清除", async () => {
    const clock = fakeClock();
    const store = new InMemorySessionStore({ ttlMs: 1000, now: clock.now });
    const { sessionToken } = await store.create(connection());

    clock.advance(800);
    expect(await store.touch(sessionToken)).toBeDefined();

    clock.advance(900); // 距上次访问 900ms：因续期仍存活。
    expect(await store.touch(sessionToken)).toBeDefined();

    clock.advance(1001); // 超过 TTL 不访问：过期。
    expect(await store.touch(sessionToken)).toBeUndefined();
    expect(store.size()).toBe(0);
  });

  it("长 turn 不因 idle TTL 被静默删除：in-flight 会话保活，收尾可写回并续期", async () => {
    const clock = fakeClock();
    const store = new InMemorySessionStore({ ttlMs: 1000, now: clock.now });
    const { sessionToken } = await store.create(connection());

    expect(await store.beginTurn(sessionToken)).toBe("ok");
    // turn 处理耗时远超 TTL。
    clock.advance(5000);

    // saveMessages / endTurn 仍然可达。
    await store.saveMessages(sessionToken, [
      { role: "system", content: "s" },
      { role: "user", content: "u" },
    ]);
    await store.endTurn(sessionToken);

    // 结束后已续期：会话仍存活且历史在。
    const snapshot = await store.touch(sessionToken);
    expect(snapshot).toBeDefined();
    expect(snapshot!.messages).toHaveLength(2);
    expect(snapshot!.version).toBe(1);

    // 结束后不再豁免：静置超 TTL 正常过期。
    clock.advance(1001);
    expect(await store.touch(sessionToken)).toBeUndefined();
  });

  it("过期会话无法 beginTurn / saveMessages", async () => {
    const clock = fakeClock();
    const store = new InMemorySessionStore({ ttlMs: 1000, now: clock.now });
    const { sessionToken } = await store.create(connection());
    clock.advance(2000);

    expect(await store.beginTurn(sessionToken)).toBe("not_found");
    await store.saveMessages(sessionToken, [{ role: "user", content: "x" }]);
    expect(await store.touch(sessionToken)).toBeUndefined();
  });

  it("beginTurn 并发互斥：进行中返回 busy，endTurn 后恢复", async () => {
    const store = new InMemorySessionStore();
    const { sessionToken } = await store.create(connection());

    expect(await store.beginTurn(sessionToken)).toBe("ok");
    expect(await store.beginTurn(sessionToken)).toBe("busy");
    await store.endTurn(sessionToken);
    expect(await store.beginTurn(sessionToken)).toBe("ok");
  });

  it("saveMessages 覆盖历史、version 递增，touch 返回副本", async () => {
    const store = new InMemorySessionStore();
    const { sessionToken } = await store.create(connection());
    await store.saveMessages(sessionToken, [{ role: "system", content: "s" }]);
    await store.saveMessages(sessionToken, [
      { role: "system", content: "s" },
      { role: "user", content: "u" },
    ]);

    const snapshot = (await store.touch(sessionToken))!;
    expect(snapshot.messages).toHaveLength(2);
    expect(snapshot.version).toBe(2);

    // 修改快照不影响存储内部状态（messages 与 config 均为副本）。
    snapshot.messages.push({ role: "user", content: "hack" });
    snapshot.config.baseUrl = "https://evil.example";
    const again = (await store.touch(sessionToken))!;
    expect(again.messages).toHaveLength(2);
    expect(again.config.baseUrl).toBe("https://llm.example/v1");
  });

  it("lastAccessAt：create 初始化，touch/beginTurn/saveMessages/endTurn 成功访问均更新，expiresAt=lastAccessAt+ttl", async () => {
    const clock = fakeClock();
    const store = new InMemorySessionStore({ ttlMs: 1000, now: clock.now });
    const { sessionToken } = await store.create(connection());

    const afterCreate = (await store.touch(sessionToken))!; // touch 本身也是一次访问
    expect(afterCreate.lastAccessAt).toBe(1_000_000);
    expect(afterCreate.expiresAt).toBe(1_001_000);

    clock.advance(300);
    const afterTouch = (await store.touch(sessionToken))!;
    expect(afterTouch.lastAccessAt).toBe(1_000_300);
    expect(afterTouch.expiresAt).toBe(1_001_300);

    clock.advance(200);
    expect(await store.beginTurn(sessionToken)).toBe("ok");
    let snap = (await store.touch(sessionToken))!;
    expect(snap.lastAccessAt).toBe(1_000_500);
    expect(snap.expiresAt).toBe(1_001_500);

    clock.advance(200);
    await store.saveMessages(sessionToken, [{ role: "user", content: "u" }]);
    snap = (await store.touch(sessionToken))!;
    expect(snap.lastAccessAt).toBe(1_000_700);
    expect(snap.expiresAt).toBe(1_001_700);

    clock.advance(200);
    await store.endTurn(sessionToken);
    snap = (await store.touch(sessionToken))!;
    expect(snap.lastAccessAt).toBe(1_000_900);
    expect(snap.expiresAt).toBe(1_001_900);
  });

  it("深拷贝隔离：调用方修改传入或快照中的 message 对象都不污染 Store 内部记录", async () => {
    const store = new InMemorySessionStore();
    const { sessionToken } = await store.create(connection());

    // 1) 传入 saveMessages 的 message 对象，事后被调用方修改，不应影响内部记录。
    const input = [{ role: "user" as const, content: "原始内容" }];
    await store.saveMessages(sessionToken, input);
    input[0]!.content = "被调用方篡改";

    const snap1 = (await store.touch(sessionToken))!;
    expect(snap1.messages[0]?.content).toBe("原始内容");

    // 2) 快照中的 message 对象被修改，也不应影响内部记录（结构化深拷贝，非浅复制数组）。
    snap1.messages[0]!.content = "篡改快照";
    const snap2 = (await store.touch(sessionToken))!;
    expect(snap2.messages[0]?.content).toBe("原始内容");
  });

  it("delete 幂等：首次 true，其后 false，token 立即失效", async () => {
    const store = new InMemorySessionStore();
    const { sessionToken } = await store.create(connection());

    expect(await store.delete(sessionToken)).toBe(true);
    expect(await store.delete(sessionToken)).toBe(false);
    expect(await store.touch(sessionToken)).toBeUndefined();
  });

  it("未知 token 一律未命中", async () => {
    const store = new InMemorySessionStore();
    await store.create(connection());
    expect(await store.touch("forged-token")).toBeUndefined();
    expect(await store.beginTurn("forged-token")).toBe("not_found");
    expect(await store.delete("forged-token")).toBe(false);
  });
});
