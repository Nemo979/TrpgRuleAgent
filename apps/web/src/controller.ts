/**
 * 可测试的控制器（apps/web/src/controller.ts）。
 *
 * 设计目标：
 * - 不接触任何 DOM；只依赖注入的 ChatClient 接口与 dispatch 回调；
 * - 通过依赖注入可被单测（传入 fake client），无需真实网络；
 * - runTurn 的 apiKey 用完即弃，本控制器不保存凭据。
 *
 * 注意：凭据（apiKey）由上层（main.ts）在内存中暂存，每轮 sendTurn 时传入；
 * 控制器本身不持有它，避免任何意外落盘风险。
 */
import type { Action } from "./state.ts";
import type {
  GatewayEvent,
  GatewaySessionStatus,
  SessionCreateOptions,
} from "./types.ts";

/** 控制器依赖的客户端接口（结构化兼容 @trpg-rule-agent/gateway-client 的 GatewayClient）。 */
export interface ChatClient {
  /** 返回安全公开快照（不含 sessionToken）。 */
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

/** dispatch 回调：把 Action 交给状态机 / 视图。 */
export type Dispatch = (action: Action) => void;

interface DescribeError {
  message: string;
  code?: string;
}

function describeError(err: unknown): DescribeError {
  if (err instanceof Error) {
    const code = (err as { code?: unknown }).code;
    return {
      message: err.message,
      ...(typeof code === "string" ? { code } : {}),
    };
  }
  return { message: String(err) };
}

/** 判断错误是否为「用户主动取消」（SDK code=aborted 或 DOM AbortError）。 */
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

/** 把 SDK/协议事件映射为 reducer 能消费的语义 Action。 */
function eventToAction(event: GatewayEvent): Action {
  switch (event.type) {
    case "turn_start":
      return { type: "event/turn_start" };
    case "text_delta":
      return { type: "event/text_delta", delta: event.delta };
    case "tool_start":
      return { type: "event/tool_start", toolCallId: event.toolCallId, toolName: event.toolName };
    case "tool_end":
      return { type: "event/tool_end", toolCallId: event.toolCallId, toolName: event.toolName };
    case "tool_error":
      return {
        type: "event/tool_error",
        toolCallId: event.toolCallId,
        toolName: event.toolName,
        error: event.error,
      };
    case "turn_end":
      return { type: "event/turn_end" };
    case "sources":
      return { type: "event/sources", sources: event.sources };
    case "error":
      return { type: "event/error", error: event.error };
    case "done":
      return { type: "event/done" };
  }
}

export class ChatController {
  readonly #client: ChatClient;
  readonly #dispatch: Dispatch;
  #abortController: AbortController | undefined;

  constructor(client: ChatClient, dispatch: Dispatch) {
    this.#client = client;
    this.#dispatch = dispatch;
  }

  /** 创建会话：透传参数给 client.createSession，并广播连接状态（安全快照，无 token）。 */
  async createSession(options: SessionCreateOptions): Promise<GatewaySessionStatus> {
    this.#dispatch({ type: "connection/connecting" });
    try {
      const res = await this.#client.createSession(options);
      this.#dispatch({
        type: "connection/connected",
        ...(res.expiresAt !== undefined ? { expiresAt: res.expiresAt } : {}),
      });
      return res;
    } catch (err) {
      const { message, code } = describeError(err);
      this.#dispatch({ type: "connection/error", message, ...(code !== undefined ? { code } : {}) });
      throw err;
    }
  }

  /**
   * 发送一轮消息：
   * - 先把用户消息作为 UI 动作派发（进入消息列表）；
   * - 消费 runTurn 的异步事件流，逐事件派发；
   * - apiKey 仅作为局部变量传入本次请求，函数返回后无引用残留。
   */
  async sendTurn(input: string, apiKey: string): Promise<void> {
    const text = input.trim();
    if (text.length === 0) {
      return;
    }

    this.#dispatch({
      type: "ui/user_message",
      id: `user-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      content: text,
    });

    this.#abortController = new AbortController();
    try {
      for await (const event of this.#client.runTurn(text, {
        apiKey,
        signal: this.#abortController.signal,
      })) {
        this.#dispatch(eventToAction(event));
      }
    } catch (err) {
      if (isAbortError(err) || this.#abortController?.signal.aborted) {
        // 用户主动停止：固化部分输出、结束生成态，不显示为连接异常。
        this.#dispatch({ type: "ui/turn_aborted" });
      } else {
        // runTurn 抛错（网络/协议等）：广播为连接错误，UI 错误区展示。
        const { message, code } = describeError(err);
        this.#dispatch({ type: "connection/error", message, ...(code !== undefined ? { code } : {}) });
      }
    } finally {
      this.#abortController = undefined;
    }
  }

  /** 停止生成：取消当前进行中的 turn（外部信号），并透传到 client.abort()。 */
  abort(): void {
    this.#abortController?.abort();
    this.#client.abort();
  }

  /** 新会话：best-effort 通知服务端删除会话，重置本地上下文。 */
  async newSession(): Promise<void> {
    this.abort();
    try {
      await this.#client.reset();
    } catch {
      // best-effort：忽略删除失败。
    }
    this.#dispatch({ type: "ui/new_session" });
  }

  /** 本地断开：清空内存会话状态，重置上下文（不发网络请求）。 */
  disconnect(): void {
    this.abort();
    this.#client.disconnect();
    this.#dispatch({ type: "ui/new_session" });
  }
}
