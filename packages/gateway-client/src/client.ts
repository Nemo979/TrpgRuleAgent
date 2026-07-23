import { GatewayClientError } from "./errors.ts";
import { decodeGatewayEvent, parseSseFrames } from "./sse.ts";
import { TransportError } from "./transport.ts";
import type { GatewayTransport, TransportRequest } from "./transport.ts";
import type { GatewayEvent, SessionCreateOptions } from "./protocol.ts";

export interface GatewayClientOptions {
  /** 传输实现（注入 BrowserTransport / 测试用 mock / 未来 WeChatTransport）。 */
  transport: GatewayTransport;
}

/** 会话公开状态快照。刻意不含 sessionToken 与任何凭据。 */
export interface GatewaySessionStatus {
  connected: boolean;
  /** 会话过期时间（ISO 字符串）。 */
  expiresAt?: string;
  /** 是否有进行中的 turn。 */
  turnInFlight: boolean;
}

export interface RunTurnOptions {
  /** 本次 turn 的模型 API Key，仅经请求头传递、用完即弃。 */
  apiKey: string;
  /** 外部取消信号（会与内部 abort() 合并）。 */
  signal?: AbortSignal;
}

interface ActiveSession {
  /** 原始 session token：仅保存在闭包私有字段，绝不进入快照/日志/URL。 */
  token: string;
  expiresAt: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/**
 * GatewayClient：面向宿主 UI 的高层 API。
 *
 * 安全不变量：
 * - API Key 从不存储在实例上：runTurn 的 apiKey 只作为局部变量传入请求头；
 * - session token 保存在 #session 私有字段（#private，运行时不可枚举/反射），
 *   刷新页面即随实例销毁而丢失；快照/status 绝不暴露它；
 * - 不触碰 localStorage/sessionStorage/IndexedDB/cookie；
 * - 客户端级并发防护：同一实例同时只允许一个进行中的 turn。
 */
export class GatewayClient {
  readonly #transport: GatewayTransport;
  #session: ActiveSession | undefined;
  #turnInFlight = false;
  /** 当前进行中 turn 的 abort 控制器（用于 abort()）。 */
  #activeController: AbortController | undefined;

  constructor(options: GatewayClientOptions) {
    this.#transport = options.transport;
  }

  /** 是否已建立会话。 */
  get connected(): boolean {
    return this.#session !== undefined;
  }

  /** 是否有进行中的 turn。 */
  get turnInFlight(): boolean {
    return this.#turnInFlight;
  }

  /**
   * 公开状态快照。绝不包含 sessionToken 或 apiKey。
   * 用于 UI 渲染与调试展示，可安全地 JSON.stringify。
   */
  status(): GatewaySessionStatus {
    const status: GatewaySessionStatus = {
      connected: this.#session !== undefined,
      turnInFlight: this.#turnInFlight,
    };
    if (this.#session) {
      status.expiresAt = this.#session.expiresAt;
    }
    return status;
  }

  /**
   * 创建会话（POST /v1/sessions）并在内存中保存 token。
   * options 中绝不允许出现凭据字段——apiKey 只在 runTurn 时传。
   *
   * 返回值刻意为安全公开快照（不含 sessionToken）：
   * 原始 token 只写入 #session 私有内存字段，绝不经公开 API 外泄；
   * 调用方（UI）只需要 connected/expiresAt 即可渲染。
   */
  async createSession(options: SessionCreateOptions): Promise<GatewaySessionStatus> {
    const body = this.#buildSessionBody(options);
    let response;
    try {
      response = await this.#transport.request({
        method: "POST",
        path: "/v1/sessions",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (error) {
      throw this.#translateTransportError(error);
    }

    const data = this.#parseJsonData(response.status, response.text);
    if (!isRecord(data) || typeof data.sessionToken !== "string" || typeof data.expiresAt !== "string") {
      throw new GatewayClientError("protocol_error", "创建会话响应缺少 sessionToken/expiresAt");
    }
    this.#session = { token: data.sessionToken, expiresAt: data.expiresAt };
    return this.status();
  }

  /**
   * 执行一轮问答（POST /v1/turns），流式产出 typed 事件。
   *
   * - 需先 createSession；否则抛 invalid_state；
   * - 客户端并发防护：已有进行中的 turn 时抛 turn_in_flight；
   * - apiKey 只经 X-Model-Api-Key 头传递，函数返回后不留引用；
   * - 迭代结束（正常/异常/取消）都会清理 in-flight 状态。
   */
  async *runTurn(input: string, options: RunTurnOptions): AsyncGenerator<GatewayEvent> {
    if (!this.#session) {
      throw new GatewayClientError("invalid_state", "尚未创建会话，请先 createSession");
    }
    if (this.#turnInFlight) {
      throw new GatewayClientError("turn_in_flight", "已有进行中的 turn");
    }
    if (typeof options.apiKey !== "string" || options.apiKey.trim() === "") {
      throw new GatewayClientError("invalid_state", "缺少模型 API Key");
    }

    const apiKey = options.apiKey;
    const token = this.#session.token;
    const controller = new AbortController();
    this.#activeController = controller;
    this.#turnInFlight = true;

    // 合并外部信号：外部 abort 时同步取消内部请求。
    const external = options.signal;
    const onExternalAbort = () => controller.abort();
    if (external) {
      if (external.aborted) {
        controller.abort();
      } else {
        external.addEventListener("abort", onExternalAbort, { once: true });
      }
    }

    try {
      const req: TransportRequest = {
        method: "POST",
        path: "/v1/turns",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
          "X-Model-Api-Key": apiKey,
        },
        body: JSON.stringify({ input }),
        signal: controller.signal,
      };

      let stream;
      try {
        stream = await this.#transport.stream(req);
      } catch (error) {
        throw this.#translateTransportError(error, apiKey);
      }

      if (stream.status < 200 || stream.status >= 300) {
        let errorText = "";
        try {
          errorText = await stream.readText();
        } catch {
          // 忽略：错误体读取失败时按状态码归类。
        }
        throw this.#httpError(stream.status, errorText, apiKey);
      }

      // 成功响应必须是 SSE：防止把 HTML 错误页/代理兜底页当事件流解析。
      if (!stream.contentType.toLowerCase().includes("text/event-stream")) {
        throw new GatewayClientError(
          "protocol_error",
          "响应 Content-Type 不是 text/event-stream",
          { status: stream.status, secret: apiKey },
        );
      }

      // 协议要求 done 恒为最后一帧；流在 done 之前结束视为被截断。
      let doneReceived = false;
      try {
        for await (const payload of parseSseFrames(stream.body)) {
          const event = decodeGatewayEvent(payload, apiKey);
          yield event;
          if (event.type === "done") {
            doneReceived = true;
            return;
          }
        }
      } catch (error) {
        if (error instanceof TransportError) {
          throw this.#translateTransportError(error, apiKey);
        }
        throw error;
      }
      if (!doneReceived) {
        throw new GatewayClientError(
          "protocol_error",
          "SSE 流在 done 事件之前提前结束",
          { secret: apiKey },
        );
      }
    } finally {
      this.#turnInFlight = false;
      this.#activeController = undefined;
      if (external) {
        external.removeEventListener("abort", onExternalAbort);
      }
    }
  }

  /** 取消进行中的 turn（若有）。安全幂等。 */
  abort(): void {
    this.#activeController?.abort();
  }

  /**
   * 删除服务端会话（DELETE /v1/session），best-effort：
   * 网络失败或 404 都视为“已断开”，只清理本地状态并吞掉错误。
   * 会先 abort 进行中的 turn。
   */
  async deleteSession(): Promise<void> {
    this.abort();
    const session = this.#session;
    this.#session = undefined;
    if (!session) {
      return;
    }
    try {
      await this.#transport.request({
        method: "DELETE",
        path: "/v1/session",
        headers: { Authorization: `Bearer ${session.token}` },
      });
    } catch {
      // best-effort：忽略删除失败。
    }
  }

  /**
   * 本地断开：清空内存中的会话状态（不发网络请求）。
   * 用于“刷新即丢失”语义或用户主动断开。
   */
  disconnect(): void {
    this.abort();
    this.#session = undefined;
  }

  /**
   * 重置会话：等价于 deleteSession（best-effort 通知服务端 + 清理本地）。
   * 语义化别名，便于 UI “新会话”按钮调用。
   */
  async reset(): Promise<void> {
    await this.deleteSession();
  }

  #buildSessionBody(options: SessionCreateOptions): Record<string, unknown> {
    const model: Record<string, unknown> = { id: options.model.id };
    if (options.model.contextWindow !== undefined) {
      model.contextWindow = options.model.contextWindow;
    }
    if (options.model.maxTokens !== undefined) {
      model.maxTokens = options.model.maxTokens;
    }
    if (options.model.reasoning !== undefined) {
      model.reasoning = options.model.reasoning;
    }
    const body: Record<string, unknown> = {
      provider: options.provider,
      model,
      baseUrl: options.baseUrl,
    };
    if (options.rulesetId !== undefined) {
      body.rulesetId = options.rulesetId;
    }
    return body;
  }

  #parseJsonData(status: number, text: string): unknown {
    if (status < 200 || status >= 300) {
      throw this.#httpError(status, text);
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new GatewayClientError("protocol_error", "响应不是合法 JSON", { status });
    }
    if (!isRecord(parsed) || !("data" in parsed)) {
      throw new GatewayClientError("protocol_error", "响应缺少 data 字段", { status });
    }
    return parsed.data;
  }

  /** 将 HTTP 非 2xx 响应翻译为 typed error（含 Gateway error code）。 */
  #httpError(status: number, text: string, secret?: string): GatewayClientError {
    let gatewayCode: string | undefined;
    let message = `请求失败（HTTP ${status}）`;
    try {
      const parsed: unknown = JSON.parse(text);
      if (isRecord(parsed) && isRecord(parsed.error)) {
        if (typeof parsed.error.code === "string") {
          gatewayCode = parsed.error.code;
        }
        if (typeof parsed.error.message === "string") {
          message = parsed.error.message;
        }
      }
    } catch {
      // 非 JSON 错误体：保留通用 message。
    }

    const options = {
      status,
      ...(gatewayCode !== undefined ? { gatewayCode } : {}),
      ...(secret !== undefined ? { secret } : {}),
    };
    if (status === 401) {
      return new GatewayClientError("unauthorized", message, options);
    }
    if (status === 409 || gatewayCode === "turn_in_flight") {
      return new GatewayClientError("turn_in_flight", message, options);
    }
    return new GatewayClientError("http_error", message, options);
  }

  #translateTransportError(error: unknown, secret?: string): GatewayClientError {
    if (error instanceof GatewayClientError) {
      return error;
    }
    if (error instanceof TransportError) {
      if (error.kind === "aborted") {
        return new GatewayClientError("aborted", "请求已取消", secret !== undefined ? { secret } : {});
      }
      return new GatewayClientError(
        "network_error",
        "网络请求失败",
        secret !== undefined ? { secret } : {},
      );
    }
    return new GatewayClientError(
      "network_error",
      "未知传输错误",
      secret !== undefined ? { secret } : {},
    );
  }
}
