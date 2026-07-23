/**
 * 应用外壳（apps/web/src/app.ts）。
 *
 * 把 main.ts 中不依赖 DOM 的部分（凭据内存管理 + client/controller 生命周期）
 * 抽出为可单测的类：
 * - API Key 仅存于 #apiKey 私有字段（页面内存），刷新即丢失；绝不落盘；
 * - 新会话/断开必须清空 #apiKey；
 * - 重复创建会话前，对旧 client 做 best-effort 清理（abort + deleteSession），
 *   且不派发任何 UI 动作，避免与新连接状态竞争。
 */
import { ChatController, type ChatClient, type Dispatch } from "./controller.ts";
import type { SessionCreateOptions } from "./types.ts";

export interface AppOptions {
  /** client 工厂（main.ts 注入真实 GatewayClient；测试注入 fake）。 */
  createClient: (gatewayBaseUrl: string) => Promise<ChatClient>;
  dispatch: Dispatch;
}

export class App {
  readonly #createClient: (gatewayBaseUrl: string) => Promise<ChatClient>;
  readonly #dispatch: Dispatch;
  #client: ChatClient | undefined;
  #controller: ChatController | undefined;
  /** 凭据仅存内存；绝不写入 storage / cookie / URL / DOM。 */
  #apiKey: string | undefined;

  constructor(options: AppOptions) {
    this.#createClient = options.createClient;
    this.#dispatch = options.dispatch;
  }

  /** 仅用于测试与 UI 判断：是否持有内存凭据。绝不返回凭据本身。 */
  get hasApiKeyInMemory(): boolean {
    return this.#apiKey !== undefined;
  }

  /**
   * 创建会话。成功返回 true（controller 已广播 connection/connected）；
   * 失败返回 false（controller 已广播 connection/error），凭据保留在内存以便重试。
   */
  async createSession(
    gatewayBaseUrl: string,
    apiKey: string,
    options: SessionCreateOptions,
  ): Promise<boolean> {
    // 重复创建前：best-effort 清理旧 client / 旧会话（不派发 UI 动作）。
    const prev = this.#client;
    this.#client = undefined;
    this.#controller = undefined;
    if (prev) {
      try {
        prev.abort();
      } catch {
        // best-effort
      }
      void prev.deleteSession().catch(() => {
        // best-effort：忽略服务端删除失败。
      });
    }

    this.#apiKey = apiKey;
    try {
      const client = await this.#createClient(gatewayBaseUrl);
      const controller = new ChatController(client, this.#dispatch);
      this.#client = client;
      this.#controller = controller;
      await controller.createSession(options);
      return true;
    } catch {
      // controller.createSession 已派发 connection/error；工厂失败时补一条。
      if (!this.#controller) {
        this.#dispatch({
          type: "connection/error",
          message: "无法初始化客户端",
        });
      }
      return false;
    }
  }

  /** 发送一轮消息：凭据从内存取出，仅作为参数传入本次调用。 */
  async sendTurn(input: string): Promise<void> {
    if (!this.#controller) {
      return;
    }
    await this.#controller.sendTurn(input, this.#apiKey ?? "");
  }

  /** 停止生成（用户主动取消，不算连接异常）。 */
  stop(): void {
    this.#controller?.abort();
  }

  /**
   * 新会话/断开：清空内存凭据 + best-effort 通知服务端删除 + 重置 UI 上下文。
   */
  async newSession(): Promise<void> {
    this.#apiKey = undefined;
    const controller = this.#controller;
    this.#controller = undefined;
    this.#client = undefined;
    if (controller) {
      await controller.newSession();
    } else {
      this.#dispatch({ type: "ui/new_session" });
    }
  }
}
