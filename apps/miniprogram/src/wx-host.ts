/**
 * 微信宿主适配边界（apps/miniprogram/src/wx-host.ts）。
 *
 * 职责：把宿主注入的 wx.request 能力封装成
 * @trpg-rule-agent/gateway-client 所需的两个最小适配器
 * （WeChatRequestAdapter / WeChatStreamAdapter）。
 *
 * 边界约束（与 SDK 侧 WeChatTransport 的契约一致）：
 * - 本文件**不引用 wx 全局对象**，也不安装微信 SDK / miniprogram-api-typings：
 *   下方的 WxHost 系列接口只是对 wx.request 形状的最小结构化声明，
 *   真实实现由开发者在微信开发者工具中用一行 `{ request: (o) => wx.request(o) }` 注入；
 * - 宿主错误对象（fail/onError 的 err，可能含 errMsg/URL 细节）**绝不透传给 SDK**：
 *   适配层在此丢弃宿主错误，只向 SDK 传递固定文案的占位错误，
 *   最终由 WeChatTransport 归一化为 TransportError("network" | "aborted")；
 * - 适配层不接触任何凭据：API Key / session token 只出现在 SDK 构造的
 *   请求头里，适配层原样转交 wx.request，不读取、不记录、不打日志。
 */
import type {
  WeChatRequestAdapter,
  WeChatStreamAdapter,
} from "@trpg-rule-agent/gateway-client";

/**
 * wx.request 返回的 RequestTask 的最小结构化声明。
 * 只声明本适配层用到的能力；字段全部可选，便于测试注入与版本兼容。
 */
export interface WxHostRequestTask {
  /** 取消请求（对应 RequestTask.abort）。 */
  abort?: () => void;
  /** 响应头就绪回调（对应 RequestTask.onHeadersReceived）。 */
  onHeadersReceived?: (
    listener: (res: { statusCode?: number; header?: Record<string, string> }) => void,
  ) => void;
  /** 分块数据回调（对应 enableChunked + RequestTask.onChunkReceived）。 */
  onChunkReceived?: (
    listener: (res: { data: ArrayBuffer | ArrayBufferView }) => void,
  ) => void;
}

/** wx.request 入参的最小结构化声明（只含本适配层会传的字段）。 */
export interface WxHostRequestOptions {
  url: string;
  method: "GET" | "POST" | "DELETE";
  header?: Record<string, string>;
  /** 已序列化的请求体（JSON 字符串），宿主不应二次 stringify。 */
  data?: string;
  /** 传 "其他" 以关闭 wx.request 的自动 JSON 解析。 */
  dataType?: string;
  /** 传 "text" 以文本形式返回响应体。 */
  responseType?: string;
  /** 流式请求需开启分块传输。 */
  enableChunked?: boolean;
  success?: (res: { statusCode?: number; data?: unknown; header?: Record<string, string> }) => void;
  fail?: (err: unknown) => void;
}

/**
 * 宿主注入接口：开发者在小程序里绑定为
 * `createWeChatAdapters({ request: (o) => wx.request(o) })`。
 * 测试中则注入纯 Node 的 fake 实现。
 */
export interface WxHost {
  request(options: WxHostRequestOptions): WxHostRequestTask | undefined | void;
}

/** createWeChatAdapters 的产物：直接喂给 WeChatTransport 的构造参数。 */
export interface WeChatAdapters {
  request: WeChatRequestAdapter;
  streamRequest: WeChatStreamAdapter;
}

/** 安全地取消底层任务（忽略取消时的次生错误）。 */
function safeAbort(task: WxHostRequestTask | undefined | void): void {
  try {
    task?.abort?.();
  } catch {
    // 忽略：abort 的次生错误没有处理价值。
  }
}

/**
 * 把宿主的 wx.request 封装为 SDK 需要的两个适配器。
 *
 * 行为要点：
 * - 普通请求：dataType:"其他" + responseType:"text" 关闭自动 JSON 解析，
 *   响应体以纯文本交回 SDK；
 * - 流式请求：enableChunked:true + onHeadersReceived / onChunkReceived；
 *   保证「先 onHeaders、后首个 chunk」的调用顺序——部分基础库版本的
 *   onHeadersReceived 不含 statusCode 或迟于首个 chunk 到达，适配层做兜底：
 *   缺 statusCode 按 200 处理；chunk 先到时**不伪造**响应头，而是把早到的
 *   chunk 暂存进 pendingChunks，待真实响应头（onHeadersReceived 或 success
 *   兜底，statusCode/content-type 全取宿主真实值）到达后按原序回放——
 *   绝不把非 2xx / 非 SSE 的真实响应误当成合法事件流；
 * - 宿主 fail/onError 的错误对象在此丢弃，只传固定文案占位错误。
 */
export function createWeChatAdapters(host: WxHost): WeChatAdapters {
  const request: WeChatRequestAdapter = (params) => {
    const options: WxHostRequestOptions = {
      url: params.url,
      method: params.method,
      dataType: "其他",
      responseType: "text",
      success: (res) => {
        params.success({ statusCode: res.statusCode ?? 0, data: res.data });
      },
      // 丢弃宿主错误对象（可能含 errMsg/URL），只传固定文案占位错误。
      fail: () => {
        params.fail(new Error("miniprogram host request failed"));
      },
    };
    if (params.header !== undefined) {
      options.header = params.header;
    }
    if (params.data !== undefined) {
      options.data = params.data;
    }

    const task = host.request(options);
    return { abort: () => safeAbort(task) };
  };

  const streamRequest: WeChatStreamAdapter = (params) => {
    let headersEmitted = false;
    // 早到 chunk 缓存：个别基础库可能在 onHeadersReceived 之前就派发首个
    // onChunkReceived。此时**绝不伪造**状态码/内容类型（那会把真实的非 2xx
    // 或非 SSE 响应误当成合法事件流），而是把 chunk 暂存，待真实响应头到达
    // （onHeadersReceived 或 success 兜底）后按原序回放，保证「先 onHeaders
    // 后首个 chunk」且状态码/内容类型全部来自宿主真实响应。
    const pendingChunks: Array<ArrayBuffer | ArrayBufferView> = [];

    const flushPending = () => {
      const buffered = pendingChunks.splice(0, pendingChunks.length);
      for (const chunk of buffered) {
        params.onChunkReceived(chunk);
      }
    };

    const emitHeaders = (info: { statusCode: number; header?: Record<string, string> }) => {
      if (headersEmitted) {
        return;
      }
      headersEmitted = true;
      params.onHeaders(info);
      // 响应头就绪后立即回放缓存的早到 chunk（保持到达顺序）。
      flushPending();
    };

    const options: WxHostRequestOptions = {
      url: params.url,
      method: params.method,
      enableChunked: true,
      success: (res) => {
        // 兜底：若 onHeadersReceived 从未触发（空流或个别基础库不派发响应头
        // 回调），用 success 携带的真实 statusCode / header 补发响应头，
        // 随后回放任何缓存 chunk 再收尾。仍不伪造任何字段。
        const info: { statusCode: number; header?: Record<string, string> } = {
          statusCode: res.statusCode ?? 200,
        };
        if (res.header !== undefined) {
          info.header = res.header;
        }
        emitHeaders(info);
        params.onComplete();
      },
      // 丢弃宿主错误对象（可能含 errMsg/URL），只传固定文案占位错误。
      fail: () => {
        params.onError(new Error("miniprogram host stream failed"));
      },
    };
    if (params.header !== undefined) {
      options.header = params.header;
    }
    if (params.data !== undefined) {
      options.data = params.data;
    }

    const task = host.request(options);

    task?.onHeadersReceived?.((res) => {
      const info: { statusCode: number; header?: Record<string, string> } = {
        // 部分基础库版本不含 statusCode：按 200 兜底（错误状态通常仍会携带）。
        // 注意：header（含 content-type）始终取宿主真实值，绝不伪造。
        statusCode: res.statusCode ?? 200,
      };
      if (res.header !== undefined) {
        info.header = res.header;
      }
      emitHeaders(info);
    });

    task?.onChunkReceived?.((res) => {
      if (headersEmitted) {
        params.onChunkReceived(res.data);
      } else {
        // 响应头尚未到达：暂存，等真实响应头就绪后再回放（不伪造响应头）。
        pendingChunks.push(res.data);
      }
    });

    return { abort: () => safeAbort(task) };
  };

  return { request, streamRequest };
}
