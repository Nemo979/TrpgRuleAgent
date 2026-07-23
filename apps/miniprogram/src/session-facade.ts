/**
 * 页面级会话门面（apps/miniprogram/src/session-facade.ts）。
 *
 * 面向小程序 Page 的单一入口：页面只需要
 * `facade.connect / send / stop / newSession / destroy` 五个动作，
 * 并把 onState 回调绑定到 `this.setData`，即可获得完整聊天能力。
 *
 * 安全不变量（与 apps/web 的 App/Controller 一致）：
 * - API Key 仅存于 #apiKey 私有内存字段：绝不写入 wx Storage、URL、
 *   日志或任何持久化介质；小程序切后台（onHide）不做任何持久化，
 *   页面卸载（onUnload → destroy()）必须清空；
 * - session token 由 GatewayClient 的 #private 字段持有，本门面不可见；
 * - 状态快照（MiniChatState）绝不包含凭据，可安全交给 setData / 日志；
 * - 错误文案基于内存中的 API Key 兜底脱敏（redactSecret），
 *   即便上游泄露也不会进入页面数据。
 *
 * 复用边界：SSE 解析、协议投影、错误分类全部复用
 * @trpg-rule-agent/gateway-client（GatewayClient + WeChatTransport），
 * 本文件不复制任何协议逻辑。
 */
import {
  GatewayClient,
  WeChatTransport,
  redactSecret,
} from "@trpg-rule-agent/gateway-client";
import type {
  GatewayEvent,
  GatewaySessionStatus,
  GatewaySource,
  GatewayWireError,
  SessionCreateOptions,
} from "@trpg-rule-agent/gateway-client";
import { createWeChatAdapters } from "./wx-host.ts";
import type { WxHost } from "./wx-host.ts";

/** 连接配置：网关地址 + BYOK 模型描述（凭据字段绝不允许出现在这里）。 */
export interface MiniSessionConfig {
  /** Gateway 绝对 http(s) 地址；域名需在小程序后台配置为 request 合法域名。 */
  gatewayBaseUrl: string;
  /** BYOK 模型服务端点（如 https://api.openai.com/v1），需在 Gateway 白名单内。 */
  modelBaseUrl: string;
  provider: string;
  model: {
    id: string;
    contextWindow?: number;
    maxTokens?: number;
    reasoning?: boolean;
  };
  rulesetId?: string;
}

export interface MiniChatMessage {
  role: "user" | "assistant";
  id: string;
  content: string;
}

/** 工具时间线条目：只保存 toolCallId / toolName / status，绝不保存参数与结果。 */
export interface MiniToolEntry {
  toolCallId: string;
  toolName: string;
  status: "start" | "end" | "error";
  error?: GatewayWireError;
}

export interface MiniChatError {
  message: string;
  code?: string;
}

/**
 * 页面状态快照：可直接交给 setData 渲染。
 * 刻意不含 apiKey / sessionToken；hasApiKey 只是布尔标记。
 */
export interface MiniChatState {
  connection: "disconnected" | "connecting" | "connected" | "error";
  connected: boolean;
  generating: boolean;
  hasApiKey: boolean;
  messages: MiniChatMessage[];
  /** 当前流式生成中的 assistant 文本（turn 结束后固化进 messages）。 */
  streamingText: string;
  toolTimeline: MiniToolEntry[];
  sources: GatewaySource[];
  error: MiniChatError | null;
  sessionExpiresAt?: string;
}

/** 门面依赖的客户端接口（结构化兼容 GatewayClient，测试注入 fake）。 */
export interface MiniChatClient {
  createSession(options: SessionCreateOptions): Promise<GatewaySessionStatus>;
  runTurn(
    input: string,
    options: { apiKey: string; signal?: AbortSignal },
  ): AsyncIterable<GatewayEvent>;
  abort(): void;
  disconnect(): void;
  deleteSession(): Promise<void>;
  reset(): Promise<void>;
}

export interface MiniProgramSessionOptions {
  /**
   * 状态回调：每次状态变化都会收到全新快照。
   * 页面侧典型绑定：`onState: (s) => this.setData({ chat: s })`。
   */
  onState: (state: MiniChatState) => void;
  /**
   * 真实宿主：注入 wx.request 的最小封装（见 wx-host.ts）。
   * 与 createClient 二选一；两者都提供时 createClient 优先（测试用）。
   */
  host?: WxHost;
  /** client 工厂（测试注入 fake；缺省时用 host 构造真实 GatewayClient）。 */
  createClient?: (config: MiniSessionConfig) => MiniChatClient;
}

function describeError(err: unknown): MiniChatError {
  if (err instanceof Error) {
    const code = (err as { code?: unknown }).code;
    return {
      message: err.message,
      ...(typeof code === "string" ? { code } : {}),
    };
  }
  return { message: String(err) };
}

/** 判断错误是否为「用户主动取消」。 */
function isAbortError(err: unknown): boolean {
  if (err instanceof Error) {
    if ((err as { code?: unknown }).code === "aborted") {
      return true;
    }
    if (err.name === "AbortError") {
      return true;
    }
  }
  return false;
}

function initialState(): MiniChatState {
  return {
    connection: "disconnected",
    connected: false,
    generating: false,
    hasApiKey: false,
    messages: [],
    streamingText: "",
    toolTimeline: [],
    sources: [],
    error: null,
  };
}

/**
 * MiniProgramSession：页面生命周期与 Gateway 会话的粘合层。
 *
 * 页面绑定建议：
 * - onLoad  → new MiniProgramSession({ host, onState })
 * - 「连接」 → connect(config, apiKey)
 * - 「发送」 → send(input)
 * - 「停止」 → stop()
 * - 「新会话」→ newSession()
 * - onUnload → destroy()   // 必须：清空内存凭据与会话
 */
export class MiniProgramSession {
  readonly #onState: (state: MiniChatState) => void;
  readonly #createClient: (config: MiniSessionConfig) => MiniChatClient;
  #client: MiniChatClient | undefined;
  /** 凭据仅存内存；绝不写入 Storage / URL / 日志 / 持久化介质。 */
  #apiKey: string | undefined;
  #abortController: AbortController | undefined;
  #state: MiniChatState = initialState();
  #assistantSeq = 0;
  #destroyed = false;

  constructor(options: MiniProgramSessionOptions) {
    this.#onState = options.onState;
    if (options.createClient) {
      this.#createClient = options.createClient;
    } else if (options.host) {
      const host = options.host;
      this.#createClient = (config) => {
        const adapters = createWeChatAdapters(host);
        return new GatewayClient({
          transport: new WeChatTransport({
            baseUrl: config.gatewayBaseUrl,
            request: adapters.request,
            streamRequest: adapters.streamRequest,
          }),
        });
      };
    } else {
      throw new Error("MiniProgramSession 需要 host（wx 适配注入）或 createClient（测试）");
    }
  }

  /** 仅用于测试与 UI 判断：是否持有内存凭据。绝不返回凭据本身。 */
  get hasApiKeyInMemory(): boolean {
    return this.#apiKey !== undefined;
  }

  /** 当前状态快照（不含任何凭据）。 */
  get state(): MiniChatState {
    return this.#state;
  }

  #emit(patch: Partial<MiniChatState>): void {
    this.#state = { ...this.#state, ...patch, hasApiKey: this.#apiKey !== undefined };
    if (!this.#destroyed) {
      this.#onState(this.#state);
    }
  }

  /** 把错误文案基于内存凭据兜底脱敏后写入状态。 */
  #emitError(err: unknown): void {
    const { message, code } = describeError(err);
    this.#emit({
      connection: "error",
      generating: false,
      error: {
        message: redactSecret(message, this.#apiKey),
        ...(code !== undefined ? { code } : {}),
      },
    });
  }

  /** 固化流式缓冲为一条 assistant 消息（若非空）。 */
  #finalizeTurn(extra: Partial<MiniChatState> = {}): void {
    if (this.#state.streamingText.length === 0) {
      this.#emit({ generating: false, ...extra });
      return;
    }
    const message: MiniChatMessage = {
      role: "assistant",
      id: `assistant-${this.#assistantSeq}`,
      content: this.#state.streamingText,
    };
    this.#assistantSeq += 1;
    this.#emit({
      generating: false,
      messages: [...this.#state.messages, message],
      streamingText: "",
      ...extra,
    });
  }

  /** best-effort 清理旧 client：abort + 服务端删除会话，不派发状态。 */
  #disposeClient(): void {
    const prev = this.#client;
    this.#client = undefined;
    if (!prev) {
      return;
    }
    try {
      prev.abort();
    } catch {
      // best-effort
    }
    void prev.deleteSession().catch(() => {
      // best-effort：忽略服务端删除失败。
    });
  }

  /**
   * 创建会话。重复调用会先 best-effort 清理旧 client / 旧会话。
   * 成功返回 true；失败返回 false（凭据保留在内存以便重试）。
   */
  async connect(config: MiniSessionConfig, apiKey: string): Promise<boolean> {
    this.#abortController?.abort();
    this.#abortController = undefined;
    this.#disposeClient();

    this.#apiKey = apiKey;
    this.#emit({ connection: "connecting", error: null });

    const options: SessionCreateOptions = {
      provider: config.provider,
      baseUrl: config.modelBaseUrl,
      model: {
        id: config.model.id,
        ...(config.model.contextWindow !== undefined
          ? { contextWindow: config.model.contextWindow }
          : {}),
        ...(config.model.maxTokens !== undefined ? { maxTokens: config.model.maxTokens } : {}),
        ...(config.model.reasoning !== undefined ? { reasoning: config.model.reasoning } : {}),
      },
      ...(config.rulesetId !== undefined ? { rulesetId: config.rulesetId } : {}),
    };

    let client: MiniChatClient | undefined;
    try {
      client = this.#createClient(config);
      const status = await client.createSession(options);
      this.#client = client;
      this.#emit({
        connection: "connected",
        connected: true,
        error: null,
        ...(status.expiresAt !== undefined ? { sessionExpiresAt: status.expiresAt } : {}),
      });
      return true;
    } catch (err) {
      // createSession 可能已在服务端创建会话后才失败（例如响应解析错误）。
      // 此时 client 尚未写入 #client，必须对局部实例做 best-effort 清理，
      // 避免服务端会话和内存 token 残留。
      if (client) {
        try {
          client.abort();
        } catch {
          // best-effort
        }
        void client.deleteSession().catch(() => {
          // best-effort：忽略服务端删除失败。
        });
      }
      this.#emitError(err);
      return false;
    }
  }

  /**
   * 发送一轮消息：凭据从内存取出，仅作为参数传入本次 runTurn。
   * 流式事件在此累加进状态快照（text_delta → streamingText 等）。
   */
  async send(input: string): Promise<void> {
    const text = input.trim();
    const client = this.#client;
    const apiKey = this.#apiKey;
    if (text.length === 0 || !client || apiKey === undefined || this.#state.generating) {
      return;
    }

    this.#emit({
      messages: [
        ...this.#state.messages,
        {
          role: "user",
          id: `user-${Date.now()}-${Math.random().toString(36).slice(2)}`,
          content: text,
        },
      ],
    });

    const controller = new AbortController();
    this.#abortController = controller;
    try {
      for await (const event of client.runTurn(text, { apiKey, signal: controller.signal })) {
        this.#applyEvent(event);
      }
    } catch (err) {
      if (isAbortError(err) || controller.signal.aborted) {
        // 用户主动停止：固化部分输出、结束生成态，不算连接异常。
        this.#finalizeTurn();
      } else {
        this.#finalizeTurn();
        this.#emitError(err);
      }
    } finally {
      if (this.#abortController === controller) {
        this.#abortController = undefined;
      }
    }
  }

  #applyEvent(event: GatewayEvent): void {
    switch (event.type) {
      case "turn_start":
        this.#emit({
          generating: true,
          streamingText: "",
          toolTimeline: [],
          sources: [],
          error: null,
        });
        return;
      case "text_delta":
        this.#emit({ streamingText: this.#state.streamingText + event.delta });
        return;
      case "tool_start":
        this.#emit({
          toolTimeline: [
            ...this.#state.toolTimeline,
            { toolCallId: event.toolCallId, toolName: event.toolName, status: "start" },
          ],
        });
        return;
      case "tool_end":
      case "tool_error": {
        const status = event.type === "tool_end" ? ("end" as const) : ("error" as const);
        const timeline = this.#state.toolTimeline.slice();
        const idx = timeline.findIndex((e) => e.toolCallId === event.toolCallId);
        const entry: MiniToolEntry = {
          toolCallId: event.toolCallId,
          toolName: event.toolName,
          status,
          ...(event.type === "tool_error" ? { error: event.error } : {}),
        };
        if (idx >= 0) {
          timeline[idx] = entry;
        } else {
          timeline.push(entry);
        }
        this.#emit({ toolTimeline: timeline });
        return;
      }
      case "turn_end":
        this.#finalizeTurn();
        return;
      case "sources":
        this.#emit({ sources: event.sources });
        return;
      case "error":
        // Gateway 已脱敏；此处再基于内存凭据兜底一次。
        this.#finalizeTurn({
          error: {
            message: redactSecret(event.error.message, this.#apiKey),
            ...(event.error.category !== undefined ? { code: event.error.category } : {}),
          },
        });
        return;
      case "done":
        this.#emit({ generating: false });
        return;
    }
  }

  /** 停止生成：取消当前进行中的 turn（不清凭据、不断开会话）。 */
  stop(): void {
    this.#abortController?.abort();
    try {
      this.#client?.abort();
    } catch {
      // best-effort
    }
  }

  /**
   * 新会话：清空内存凭据 + best-effort 通知服务端删除 + 重置页面上下文。
   */
  async newSession(): Promise<void> {
    this.#apiKey = undefined;
    this.#abortController?.abort();
    this.#abortController = undefined;
    const client = this.#client;
    this.#client = undefined;
    if (client) {
      try {
        await client.reset();
      } catch {
        // best-effort：忽略删除失败。
      }
    }
    this.#state = initialState();
    this.#assistantSeq = 0;
    this.#emit({});
  }

  /**
   * 页面卸载清理（onUnload 必须调用）：
   * 清空内存凭据、取消进行中的 turn、best-effort 删除服务端会话。
   * 之后不再派发任何状态回调。
   */
  destroy(): void {
    this.#apiKey = undefined;
    this.#abortController?.abort();
    this.#abortController = undefined;
    this.#disposeClient();
    this.#state = initialState();
    this.#destroyed = true;
  }
}
