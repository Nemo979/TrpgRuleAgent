import { TransportError } from "./transport.ts";
import type {
  GatewayTransport,
  TransportRequest,
  TransportResponse,
  TransportStreamResponse,
} from "./transport.ts";

export interface BrowserTransportOptions {
  /**
   * Gateway 基地址。默认空字符串表示“同源相对路径”
   * （生产环境推荐用同源反向代理，避免 CORS 与凭据跨域）。
   * 末尾多余的斜杠会被去除。
   */
  baseUrl?: string;
  /**
   * 可注入的 fetch 实现（测试用）。默认使用全局 fetch。
   * 类型使用宽松签名以兼容不同宿主的 fetch 定义。
   */
  fetch?: typeof fetch;
}

/**
 * 基于原生 fetch + ReadableStream 的浏览器传输实现。
 *
 * 设计要点：
 * - 不使用 EventSource（无法带自定义头/请求体/POST）；
 * - stream() 用 fetch 的 body.getReader() 逐块产出 Uint8Array，天然支持 AbortSignal；
 * - baseUrl 默认同源相对路径；构造时不接触任何凭据。
 */
export class BrowserTransport implements GatewayTransport {
  readonly #baseUrl: string;
  readonly #fetch: typeof fetch;

  constructor(options: BrowserTransportOptions = {}) {
    this.#baseUrl = (options.baseUrl ?? "").replace(/\/+$/, "");
    const impl = options.fetch ?? (globalThis.fetch as typeof fetch | undefined);
    if (typeof impl !== "function") {
      throw new TransportError("network", "当前环境不支持 fetch");
    }
    // 绑定到 globalThis，避免 “Illegal invocation”。
    this.#fetch = options.fetch ?? impl.bind(globalThis);
  }

  #url(path: string): string {
    return `${this.#baseUrl}${path}`;
  }

  async request(req: TransportRequest): Promise<TransportResponse> {
    let response: Response;
    try {
      response = await this.#fetch(this.#url(req.path), this.#init(req));
    } catch (error) {
      throw this.#toTransportError(error);
    }
    let text = "";
    try {
      text = await response.text();
    } catch (error) {
      throw this.#toTransportError(error);
    }
    return { status: response.status, text };
  }

  async stream(req: TransportRequest): Promise<TransportStreamResponse> {
    let response: Response;
    try {
      response = await this.#fetch(this.#url(req.path), this.#init(req));
    } catch (error) {
      throw this.#toTransportError(error);
    }

    const contentType = response.headers.get("content-type") ?? "";
    const bodyStream = response.body;
    const toTransportError = this.#toTransportError.bind(this);

    async function* iterate(): AsyncGenerator<Uint8Array> {
      if (!bodyStream) {
        return;
      }
      const reader = bodyStream.getReader();
      try {
        for (;;) {
          let chunk: ReadableStreamReadResult<Uint8Array>;
          try {
            chunk = await reader.read();
          } catch (error) {
            throw toTransportError(error);
          }
          if (chunk.done) {
            return;
          }
          if (chunk.value) {
            yield chunk.value;
          }
        }
      } finally {
        // 迭代提前结束（return/throw/abort）时释放底层流。
        try {
          await reader.cancel();
        } catch {
          // 忽略取消时的次生错误。
        }
        reader.releaseLock();
      }
    }

    return {
      status: response.status,
      contentType,
      body: iterate(),
      readText: async () => {
        try {
          return await response.text();
        } catch (error) {
          throw toTransportError(error);
        }
      },
    };
  }

  #init(req: TransportRequest): RequestInit {
    const init: RequestInit = {
      method: req.method,
      // 禁止自动重定向：防止 30x 把带凭据的请求引到非预期端点。
      redirect: "error",
      // 同源反代场景无需携带 cookie；显式关闭避免误带凭据。
      credentials: "omit",
    };
    if (req.headers) {
      init.headers = req.headers;
    }
    if (req.body !== undefined) {
      init.body = req.body;
    }
    if (req.signal) {
      init.signal = req.signal;
    }
    return init;
  }

  #toTransportError(error: unknown): TransportError {
    if (isAbortError(error)) {
      return new TransportError("aborted", "请求已取消");
    }
    // 归一化：绝不透出底层 message（可能含 URL/头信息）。
    return new TransportError("network", "网络请求失败");
  }
}

function isAbortError(error: unknown): boolean {
  return (
    error instanceof Error &&
    (error.name === "AbortError" || error.name === "TimeoutError")
  );
}
