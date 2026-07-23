import { TransportError } from "./transport.ts";
import type {
  GatewayTransport,
  TransportRequest,
  TransportResponse,
  TransportStreamResponse,
} from "./transport.ts";

/**
 * WeChatTransport：面向微信小程序宿主的可选传输适配层。
 *
 * 核心约束：
 * - 不引入微信 SDK / Node SDK / 任何运行时第三方依赖；
 * - 不依赖 wx 全局对象：所有微信能力由宿主（apps/miniprogram 等）
 *   封装成下述最小接口后注入；
 * - 不假设 fetch / ReadableStream / Node stream 存在；
 * - 错误一律归一化为 TransportError("network" | "aborted")，
 *   绝不携带 URL、请求头、API Key 或底层 cause；
 * - 构造与运行期不接触任何凭据：API Key 仅由 GatewayClient.runTurn
 *   每次调用经请求头传入，用完即弃。
 */

/** 请求任务句柄：宿主返回的对象至少应支持取消（对应 wx RequestTask.abort）。 */
export interface WeChatRequestTask {
  abort?: () => void;
}

/** 普通请求适配器入参（由 WeChatTransport 构造并传给宿主）。 */
export interface WeChatRequestParams {
  /** 已由 baseUrl + path 拼接的绝对地址。 */
  url: string;
  method: "GET" | "POST" | "DELETE";
  header?: Record<string, string>;
  /** 已序列化的请求体（JSON 字符串），宿主不应二次 stringify。 */
  data?: string;
  /**
   * 成功回调。data 建议为字符串响应体（宿主应设 dataType:"text" 或
   * 等价手段关闭自动 JSON 解析）；若宿主传入已解析对象，
   * WeChatTransport 会兜底重新序列化为文本。
   */
  success: (res: { statusCode: number; data: unknown }) => void;
  /** 传输层失败回调（断网/超时/被 abort 等）。 */
  fail: (err: unknown) => void;
}

/**
 * 普通请求适配器：宿主用 wx.request 封装成此形状注入。
 * 例如：(p) => wx.request({ ...p, dataType: "text", responseType: "text" })
 */
export type WeChatRequestAdapter = (params: WeChatRequestParams) => WeChatRequestTask | void;

/** 流式请求适配器入参。 */
export interface WeChatStreamParams {
  url: string;
  /** Gateway 流式端点均为 POST + SSE。 */
  method: "POST";
  header?: Record<string, string>;
  data?: string;
  /**
   * 响应头就绪回调（对应 wx RequestTask.onHeadersReceived）。
   * header 字段名大小写不敏感；必须先于首个 onChunkReceived 调用。
   */
  onHeaders: (info: { statusCode: number; header?: Record<string, string> }) => void;
  /** 分块字节回调（对应 enableChunked + RequestTask.onChunkReceived）。 */
  onChunkReceived: (chunk: ArrayBuffer | ArrayBufferView) => void;
  /** 流正常结束回调。 */
  onComplete: () => void;
  /** 传输层异常回调。 */
  onError: (err: unknown) => void;
}

/**
 * 流式请求适配器：宿主用 wx.request({ enableChunked: true }) +
 * RequestTask.onHeadersReceived / onChunkReceived 封装后注入。
 * WeChatTransport 将其适配为统一的 AsyncIterable<Uint8Array>。
 */
export type WeChatStreamAdapter = (params: WeChatStreamParams) => WeChatRequestTask | void;

export interface WeChatTransportOptions {
  /**
   * Gateway 基地址，必须是绝对 http(s) URL（需在小程序后台配置为合法域名）。
   * 小程序没有“同源相对路径”概念，故与 BrowserTransport 不同，此项必填。
   * 末尾多余斜杠会被去除。
   */
  baseUrl: string;
  /** 普通 JSON 请求适配器。 */
  request: WeChatRequestAdapter;
  /** 分块流式适配器。 */
  streamRequest: WeChatStreamAdapter;
}

/** 内部字节队列：把回调风格的分块推送适配为异步迭代。 */
class ByteQueue {
  #chunks: Uint8Array[] = [];
  #done = false;
  #error: TransportError | undefined;
  #waiters: Array<() => void> = [];

  push(chunk: Uint8Array): void {
    if (this.#done) {
      return;
    }
    this.#chunks.push(chunk);
    this.#notify();
  }

  /** 异常结束：已入队字节仍会先被消费，之后抛出该错误。 */
  fail(error: TransportError): void {
    if (this.#done) {
      return;
    }
    this.#error = error;
    this.#done = true;
    this.#notify();
  }

  complete(): void {
    if (this.#done) {
      return;
    }
    this.#done = true;
    this.#notify();
  }

  get settled(): boolean {
    return this.#done;
  }

  #notify(): void {
    const waiters = this.#waiters;
    this.#waiters = [];
    for (const waiter of waiters) {
      waiter();
    }
  }

  async next(): Promise<IteratorResult<Uint8Array, undefined>> {
    for (;;) {
      const chunk = this.#chunks.shift();
      if (chunk !== undefined) {
        return { value: chunk, done: false };
      }
      if (this.#error !== undefined) {
        throw this.#error;
      }
      if (this.#done) {
        return { value: undefined, done: true };
      }
      await new Promise<void>((resolve) => this.#waiters.push(resolve));
    }
  }
}

/** 将宿主给的 ArrayBuffer/视图归一化为独立的 Uint8Array（复制，防复用缓冲被改写）。 */
function normalizeChunk(chunk: ArrayBuffer | ArrayBufferView): Uint8Array {
  if (ArrayBuffer.isView(chunk)) {
    return new Uint8Array(
      chunk.buffer.slice(chunk.byteOffset, chunk.byteOffset + chunk.byteLength),
    );
  }
  return new Uint8Array(chunk.slice(0));
}

/** 宿主可能开启了自动 JSON 解析：兜底把响应体还原为文本。 */
function toText(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (value === undefined || value === null) {
    return "";
  }
  try {
    return JSON.stringify(value);
  } catch {
    return "";
  }
}

/** 大小写不敏感读取响应头。 */
function getHeader(headers: Record<string, string> | undefined, name: string): string {
  if (!headers) {
    return "";
  }
  const lower = name.toLowerCase();
  for (const key of Object.keys(headers)) {
    if (key.toLowerCase() === lower) {
      return headers[key] ?? "";
    }
  }
  return "";
}

/**
 * 基于宿主注入适配器的微信小程序传输实现。
 * 行为与 BrowserTransport 保持同一 GatewayTransport 契约：
 * - request：普通 JSON 请求，返回 { status, text }；
 * - stream：POST + SSE，返回 { status, contentType, body, readText }；
 * - AbortSignal 取消 -> TransportError("aborted")；
 * - 其余传输层失败 -> TransportError("network")，消息为固定文案。
 */
export class WeChatTransport implements GatewayTransport {
  readonly #baseUrl: string;
  readonly #requestAdapter: WeChatRequestAdapter;
  readonly #streamAdapter: WeChatStreamAdapter;

  constructor(options: WeChatTransportOptions) {
    const baseUrl = (options.baseUrl ?? "").replace(/\/+$/, "");
    if (!/^https?:\/\//i.test(baseUrl)) {
      throw new TransportError("network", "WeChatTransport 需要绝对 http(s) baseUrl");
    }
    if (typeof options.request !== "function") {
      throw new TransportError("network", "WeChatTransport 缺少 request 适配器");
    }
    if (typeof options.streamRequest !== "function") {
      throw new TransportError("network", "WeChatTransport 缺少 streamRequest 适配器");
    }
    this.#baseUrl = baseUrl;
    this.#requestAdapter = options.request;
    this.#streamAdapter = options.streamRequest;
  }

  #url(path: string): string {
    return `${this.#baseUrl}${path}`;
  }

  async request(req: TransportRequest): Promise<TransportResponse> {
    const signal = req.signal;
    if (signal?.aborted) {
      throw new TransportError("aborted", "请求已取消");
    }

    return await new Promise<TransportResponse>((resolve, reject) => {
      let settled = false;
      let task: WeChatRequestTask | void;

      const onAbort = () => {
        if (settled) {
          return;
        }
        settled = true;
        try {
          task?.abort?.();
        } catch {
          // 忽略取消时的次生错误。
        }
        reject(new TransportError("aborted", "请求已取消"));
      };

      const finish = (action: () => void) => {
        if (settled) {
          return;
        }
        settled = true;
        if (signal) {
          signal.removeEventListener("abort", onAbort);
        }
        action();
      };

      if (signal) {
        signal.addEventListener("abort", onAbort, { once: true });
      }

      const params: WeChatRequestParams = {
        url: this.#url(req.path),
        method: req.method,
        success: (res) => {
          finish(() => resolve({ status: res.statusCode, text: toText(res.data) }));
        },
        fail: () => {
          // 归一化：不读取、不透传宿主错误对象（可能含 URL/errMsg 细节）。
          finish(() =>
            reject(
              signal?.aborted
                ? new TransportError("aborted", "请求已取消")
                : new TransportError("network", "网络请求失败"),
            ),
          );
        },
      };
      if (req.headers !== undefined) {
        params.header = req.headers;
      }
      if (req.body !== undefined) {
        params.data = req.body;
      }

      try {
        task = this.#requestAdapter(params);
      } catch {
        finish(() => reject(new TransportError("network", "网络请求失败")));
        return;
      }

      // 竞态修复：signal 可能在 adapter 执行期间同步 abort——
      // 此时 onAbort 已触发但 task 尚未赋值，底层任务不会被取消。
      // adapter 返回后立刻补检：若已取消则 abort 任务并保持 aborted 结果。
      if (signal?.aborted) {
        try {
          task?.abort?.();
        } catch {
          // 忽略取消时的次生错误。
        }
        // 若 onAbort 未及 reject（防御），保证仍以 aborted 收尾；已 settled 时为 no-op。
        finish(() => reject(new TransportError("aborted", "请求已取消")));
      }
    });
  }

  async stream(req: TransportRequest): Promise<TransportStreamResponse> {
    const signal = req.signal;
    if (signal?.aborted) {
      throw new TransportError("aborted", "请求已取消");
    }

    const queue = new ByteQueue();
    let task: WeChatRequestTask | void;
    let headersSettled = false;
    let resolveHeaders: (info: { statusCode: number; header?: Record<string, string> }) => void =
      () => {};
    let rejectHeaders: (error: TransportError) => void = () => {};
    const headersPromise = new Promise<{ statusCode: number; header?: Record<string, string> }>(
      (resolve, reject) => {
        resolveHeaders = (info) => {
          headersSettled = true;
          resolve(info);
        };
        rejectHeaders = (error) => {
          headersSettled = true;
          reject(error);
        };
      },
    );

    const failAll = (error: TransportError) => {
      if (!headersSettled) {
        rejectHeaders(error);
      }
      queue.fail(error);
    };

    const onAbort = () => {
      try {
        task?.abort?.();
      } catch {
        // 忽略取消时的次生错误。
      }
      failAll(new TransportError("aborted", "请求已取消"));
    };
    if (signal) {
      signal.addEventListener("abort", onAbort, { once: true });
    }
    const detachSignal = () => {
      if (signal) {
        signal.removeEventListener("abort", onAbort);
      }
    };

    const params: WeChatStreamParams = {
      url: this.#url(req.path),
      method: "POST",
      onHeaders: (info) => {
        if (!headersSettled) {
          resolveHeaders(info);
        }
      },
      onChunkReceived: (chunk) => queue.push(normalizeChunk(chunk)),
      onComplete: () => {
        if (!headersSettled) {
          // 未收到响应头即结束：视为传输层异常。
          failAll(new TransportError("network", "网络请求失败"));
          return;
        }
        queue.complete();
      },
      onError: () => {
        // 归一化：不读取、不透传宿主错误对象。
        failAll(
          signal?.aborted
            ? new TransportError("aborted", "请求已取消")
            : new TransportError("network", "网络请求失败"),
        );
      },
    };
    if (req.headers !== undefined) {
      params.header = req.headers;
    }
    if (req.body !== undefined) {
      params.data = req.body;
    }

    try {
      task = this.#streamAdapter(params);
    } catch {
      detachSignal();
      throw new TransportError("network", "网络请求失败");
    }

    // 竞态修复：signal 可能在 adapter 执行期间同步 abort——
    // 此时 onAbort 已触发（failAll aborted）但 task 尚未赋值，底层任务不会被取消。
    // adapter 返回后立刻补检：若已取消则 abort 任务并保持 aborted 错误。
    if (signal?.aborted) {
      try {
        task?.abort?.();
      } catch {
        // 忽略取消时的次生错误。
      }
      failAll(new TransportError("aborted", "请求已取消"));
    }

    let headers: { statusCode: number; header?: Record<string, string> };
    try {
      headers = await headersPromise;
    } catch (error) {
      detachSignal();
      throw error;
    }

    // 迭代结束（正常/异常/提前 return）时：释放 abort 监听；
    // 若流尚未结束（提前退出）则取消底层任务，避免继续下载。
    async function* iterate(): AsyncGenerator<Uint8Array> {
      try {
        for (;;) {
          const result = await queue.next();
          if (result.done) {
            return;
          }
          yield result.value;
        }
      } finally {
        detachSignal();
        if (!queue.settled) {
          try {
            task?.abort?.();
          } catch {
            // 忽略取消时的次生错误。
          }
          queue.fail(new TransportError("aborted", "请求已取消"));
        }
      }
    }

    const body = iterate();

    return {
      status: headers.statusCode,
      contentType: getHeader(headers.header, "content-type"),
      body,
      readText: async () => {
        const decoder = new TextDecoder();
        let text = "";
        for await (const chunk of body) {
          text += decoder.decode(chunk, { stream: true });
        }
        text += decoder.decode();
        return text;
      },
    };
  }
}
