import { describe, expect, it } from "vitest";
import type { GatewayEvent, SessionCreateOptions } from "@trpg-rule-agent/gateway-client";
import { MiniProgramSession } from "../src/session-facade.ts";
import type { MiniChatClient, MiniChatState, MiniSessionConfig } from "../src/session-facade.ts";

const API_KEY = "sk-secret-key-42";

const CONFIG: MiniSessionConfig = {
  gatewayBaseUrl: "https://gw.example.com",
  modelBaseUrl: "https://api.example.com/v1",
  provider: "openai",
  model: { id: "gpt-test" },
  rulesetId: "pathfinder-1e",
};

/** 轮询等待条件成立（微任务/宏任务混合驱动，带超时保护）。 */
async function waitUntil(cond: () => boolean, timeoutMs = 1000): Promise<void> {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) {
      throw new Error("waitUntil 超时");
    }
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

interface FakeClientOptions {
  createSessionImpl?: (options: SessionCreateOptions) => Promise<{ connected: boolean; turnInFlight: boolean; expiresAt?: string }>;
  runTurnImpl?: (
    input: string,
    options: { apiKey: string; signal?: AbortSignal },
  ) => AsyncGenerator<GatewayEvent>;
}

/** 可编程 fake client：记录调用轨迹，便于断言生命周期。 */
class FakeClient implements MiniChatClient {
  readonly calls: string[] = [];
  readonly runTurnKeys: string[] = [];
  readonly sessionOptions: SessionCreateOptions[] = [];
  readonly #options: FakeClientOptions;

  constructor(options: FakeClientOptions = {}) {
    this.#options = options;
  }

  async createSession(options: SessionCreateOptions) {
    this.calls.push("createSession");
    this.sessionOptions.push(options);
    if (this.#options.createSessionImpl) {
      return await this.#options.createSessionImpl(options);
    }
    return { connected: true, turnInFlight: false, expiresAt: "2026-01-01T00:00:00Z" };
  }

  runTurn(input: string, options: { apiKey: string; signal?: AbortSignal }) {
    this.calls.push("runTurn");
    this.runTurnKeys.push(options.apiKey);
    if (this.#options.runTurnImpl) {
      return this.#options.runTurnImpl(input, options);
    }
    // 默认脚本：一轮完整的流式回复。
    return (async function* (): AsyncGenerator<GatewayEvent> {
      yield { type: "turn_start" };
      yield { type: "text_delta", delta: "你好" };
      yield { type: "text_delta", delta: "，冒险者" };
      yield { type: "turn_end" };
      yield { type: "done" };
    })();
  }

  abort(): void {
    this.calls.push("abort");
  }

  disconnect(): void {
    this.calls.push("disconnect");
  }

  async deleteSession(): Promise<void> {
    this.calls.push("deleteSession");
  }

  async reset(): Promise<void> {
    this.calls.push("reset");
  }
}

function makeFacade(clients: FakeClient[]) {
  let index = 0;
  const states: MiniChatState[] = [];
  const facade = new MiniProgramSession({
    onState: (state) => states.push(state),
    createClient: () => {
      const client = clients[index];
      if (!client) {
        throw new Error("测试未准备足够的 fake client");
      }
      index += 1;
      return client;
    },
  });
  return { facade, states };
}

describe("MiniProgramSession / API Key 生命周期", () => {
  it("connect 后凭据仅存内存；send 时逐轮传给 runTurn；状态快照绝不含凭据", async () => {
    const client = new FakeClient();
    const { facade, states } = makeFacade([client]);

    expect(facade.hasApiKeyInMemory).toBe(false);
    const ok = await facade.connect(CONFIG, API_KEY);
    expect(ok).toBe(true);
    expect(facade.hasApiKeyInMemory).toBe(true);
    expect(facade.state.connected).toBe(true);
    expect(facade.state.sessionExpiresAt).toBe("2026-01-01T00:00:00Z");

    await facade.send("旅店老板卖什么？");
    expect(client.runTurnKeys).toEqual([API_KEY]);
    expect(facade.state.messages.map((m) => m.role)).toEqual(["user", "assistant"]);
    expect(facade.state.messages[1]!.content).toBe("你好，冒险者");

    // 所有状态快照可安全 JSON 序列化，绝不包含 API Key。
    for (const state of states) {
      expect(JSON.stringify(state)).not.toContain(API_KEY);
    }
  });

  it("connect 传给 createSession 的是模型端点 baseUrl 与连接配置", async () => {
    const client = new FakeClient();
    const { facade } = makeFacade([client]);
    await facade.connect(CONFIG, API_KEY);

    expect(client.sessionOptions[0]).toEqual({
      provider: "openai",
      baseUrl: "https://api.example.com/v1",
      model: { id: "gpt-test" },
      rulesetId: "pathfinder-1e",
    });
  });

  it("connect 失败时保留内存凭据以便重试，并进入 error 状态", async () => {
    const client = new FakeClient({
      createSessionImpl: async () => {
        throw new Error("网关不可达");
      },
    });
    const { facade } = makeFacade([client]);

    const ok = await facade.connect(CONFIG, API_KEY);
    expect(ok).toBe(false);
    expect(facade.hasApiKeyInMemory).toBe(true);
    expect(facade.state.connection).toBe("error");
    expect(facade.state.error?.message).toBe("网关不可达");
  });

  it("createSession 失败时清理局部 client，避免服务端会话残留", async () => {
    const client = new FakeClient({
      createSessionImpl: async () => {
        throw new Error("响应解析失败");
      },
    });
    const { facade } = makeFacade([client]);

    await expect(facade.connect(CONFIG, API_KEY)).resolves.toBe(false);
    expect(client.calls).toContain("abort");
    expect(client.calls).toContain("deleteSession");
    expect(facade.state.connection).toBe("error");
  });
});

describe("MiniProgramSession / 重复建会话", () => {
  it("重复 connect 会 best-effort 清理旧 client（abort + deleteSession）", async () => {
    const first = new FakeClient();
    const second = new FakeClient();
    const { facade } = makeFacade([first, second]);

    await facade.connect(CONFIG, API_KEY);
    await facade.connect(CONFIG, "sk-another-key");

    expect(first.calls).toContain("abort");
    expect(first.calls).toContain("deleteSession");
    expect(second.calls).toEqual(["createSession"]);
    expect(facade.state.connected).toBe(true);
  });
});

describe("MiniProgramSession / 发送与停止", () => {
  it("流式事件逐步累加：turn_start 重置缓冲，text_delta 追加，turn_end 固化", async () => {
    const deltas: string[] = [];
    const client = new FakeClient();
    const facade = new MiniProgramSession({
      onState: (state) => deltas.push(state.streamingText),
      createClient: () => client,
    });
    await facade.connect(CONFIG, API_KEY);
    await facade.send("hi");

    expect(deltas).toContain("你好");
    expect(deltas).toContain("你好，冒险者");
    expect(facade.state.streamingText).toBe("");
    expect(facade.state.generating).toBe(false);
  });

  it("空输入 / 未连接 / 生成中：send 直接忽略", async () => {
    const client = new FakeClient();
    const { facade } = makeFacade([client]);

    await facade.send("未连接时发送");
    expect(client.calls).not.toContain("runTurn");

    await facade.connect(CONFIG, API_KEY);
    await facade.send("   ");
    expect(client.calls.filter((c) => c === "runTurn")).toHaveLength(0);
  });

  it("stop 停止生成：固化部分输出、不产生错误、透传 client.abort", async () => {
    const client = new FakeClient({
      runTurnImpl: (_input, options) =>
        (async function* (): AsyncGenerator<GatewayEvent> {
          yield { type: "turn_start" };
          yield { type: "text_delta", delta: "生成到一半" };
          await new Promise<void>((resolve) => {
            const signal = options.signal;
            if (!signal || signal.aborted) {
              resolve();
              return;
            }
            signal.addEventListener("abort", () => resolve(), { once: true });
          });
          const err = new Error("请求已取消");
          (err as { code?: string }).code = "aborted";
          throw err;
        })(),
    });
    const { facade } = makeFacade([client]);
    await facade.connect(CONFIG, API_KEY);

    const sendPromise = facade.send("讲个故事");
    await waitUntil(() => facade.state.streamingText === "生成到一半");
    facade.stop();
    await sendPromise;

    expect(client.calls).toContain("abort");
    expect(facade.state.generating).toBe(false);
    expect(facade.state.error).toBeNull();
    const assistant = facade.state.messages.find((m) => m.role === "assistant");
    expect(assistant?.content).toBe("生成到一半");
  });
});

describe("MiniProgramSession / 新会话与卸载清理", () => {
  it("newSession 清空内存凭据、best-effort reset、重置页面上下文", async () => {
    const client = new FakeClient();
    const { facade } = makeFacade([client]);
    await facade.connect(CONFIG, API_KEY);
    await facade.send("hi");

    await facade.newSession();

    expect(facade.hasApiKeyInMemory).toBe(false);
    expect(client.calls).toContain("reset");
    expect(facade.state.messages).toEqual([]);
    expect(facade.state.connected).toBe(false);
    expect(facade.state.connection).toBe("disconnected");
  });

  it("destroy（页面卸载）清空凭据与会话，且之后不再派发状态回调", async () => {
    const client = new FakeClient();
    const { facade, states } = makeFacade([client]);
    await facade.connect(CONFIG, API_KEY);

    const before = states.length;
    facade.destroy();

    expect(facade.hasApiKeyInMemory).toBe(false);
    expect(client.calls).toContain("abort");
    expect(client.calls).toContain("deleteSession");
    expect(states.length).toBe(before);
    expect(facade.state.messages).toEqual([]);
  });

  it("destroy 打断进行中的 send，且不再触发任何状态回调", async () => {
    const client = new FakeClient({
      runTurnImpl: (_input, options) =>
        (async function* (): AsyncGenerator<GatewayEvent> {
          yield { type: "turn_start" };
          yield { type: "text_delta", delta: "半" };
          await new Promise<void>((resolve) => {
            const signal = options.signal;
            if (!signal || signal.aborted) {
              resolve();
              return;
            }
            signal.addEventListener("abort", () => resolve(), { once: true });
          });
          const err = new Error("请求已取消");
          (err as { code?: string }).code = "aborted";
          throw err;
        })(),
    });
    const { facade, states } = makeFacade([client]);
    await facade.connect(CONFIG, API_KEY);

    const sendPromise = facade.send("hi");
    await waitUntil(() => states.some((s) => s.streamingText === "半"));
    const before = states.length;
    facade.destroy();
    await sendPromise;

    expect(states.length).toBe(before);
    expect(facade.hasApiKeyInMemory).toBe(false);
  });
});

describe("MiniProgramSession / 异常脱敏", () => {
  it("runTurn 抛错文案中的 API Key 被兜底脱敏后才进入状态", async () => {
    const client = new FakeClient({
      runTurnImpl: () =>
        (async function* (): AsyncGenerator<GatewayEvent> {
          throw new Error(`上游拒绝：key ${API_KEY} 无效`);
        })(),
    });
    const { facade, states } = makeFacade([client]);
    await facade.connect(CONFIG, API_KEY);
    await facade.send("hi");

    expect(facade.state.error?.message).toContain("[REDACTED]");
    expect(facade.state.error?.message).not.toContain(API_KEY);
    for (const state of states) {
      expect(JSON.stringify(state)).not.toContain(API_KEY);
    }
  });

  it("SSE error 事件文案中的 API Key 同样被兜底脱敏", async () => {
    const client = new FakeClient({
      runTurnImpl: () =>
        (async function* (): AsyncGenerator<GatewayEvent> {
          yield { type: "turn_start" };
          yield {
            type: "error",
            error: { category: "provider_error", message: `provider 报错 ${API_KEY}` },
          };
          yield { type: "done" };
        })(),
    });
    const { facade } = makeFacade([client]);
    await facade.connect(CONFIG, API_KEY);
    await facade.send("hi");

    expect(facade.state.error?.message).toContain("[REDACTED]");
    expect(facade.state.error?.message).not.toContain(API_KEY);
    expect(facade.state.error?.code).toBe("provider_error");
  });

  it("connect 失败文案中的 API Key 被兜底脱敏", async () => {
    const client = new FakeClient({
      createSessionImpl: async () => {
        throw new Error(`调试信息意外包含 ${API_KEY}`);
      },
    });
    const { facade } = makeFacade([client]);
    await facade.connect(CONFIG, API_KEY);

    expect(facade.state.error?.message).toContain("[REDACTED]");
    expect(facade.state.error?.message).not.toContain(API_KEY);
  });
});
