/**
 * 仅供本地演示 / 测试的可注入 mock 传输（apps/web/src/mock-transport.ts）。
 *
 * ⚠️ 安全约束：本文件【绝不】进入生产路径。
 *   - main.ts 默认使用 BrowserTransport（真实同源网络）；
 *   - 仅当【开发模式（import.meta.env.DEV）+ URL 带 `?mock=1`】双条件同时
 *     满足时，main.ts 才动态 import 本 mock；生产构建中该分支被静态替换为
 *     false 并整体 tree-shake，本文件不会出现在生产 bundle；
 *   - 这样默认构建 / 默认访问走真实路径，演示是显式开关，不弱化安全约束。
 *
 * 它实现 @trpg-rule-agent/gateway-client 的 GatewayTransport 接口，
 * 脚本化返回一段 SSE（turn_start → text_delta… → tool_start/end → turn_end → sources → done），
 * 使整个 UI 在没有真实 Gateway 时也能端到端演示。
 */

import {
  TransportError,
  type GatewayTransport,
  type TransportRequest,
  type TransportResponse,
  type TransportStreamResponse,
} from "@trpg-rule-agent/gateway-client";

const encoder = new TextEncoder();

function sseFrame(json: unknown): Uint8Array {
  return encoder.encode(`data: ${JSON.stringify(json)}\n\n`);
}

/** 若信号已取消，立即抛出 aborted 传输错误（与真实 BrowserTransport 语义一致）。 */
function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) {
    throw new TransportError("aborted", "mock: 请求已取消");
  }
}

/**
 * 可被 AbortSignal 中断的延时：取消时立即 reject（TransportError("aborted")），
 * 并清理定时器与监听器，避免继续推进演示流。
 */
function abortableDelay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new TransportError("aborted", "mock: 请求已取消"));
      return;
    }
    const onAbort = (): void => {
      clearTimeout(timer);
      reject(new TransportError("aborted", "mock: 请求已取消"));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/** 生成一段固定的演示 SSE 事件序列。 */
function buildScript(): Uint8Array[] {
  return [
    sseFrame({ type: "turn_start" }),
    sseFrame({ type: "text_delta", delta: "借机攻击（Attack of Opportunity）" }),
    sseFrame({ type: "text_delta", delta: "在 Pathfinder 中触发于：当敌人" }),
    sseFrame({ type: "text_delta", delta: "在你的威胁范围内执行离开式移动时。" }),
    sseFrame({ type: "tool_start", toolCallId: "t1", toolName: "search_rules" }),
    sseFrame({ type: "tool_end", toolCallId: "t1", toolName: "search_rules" }),
    sseFrame({ type: "turn_end" }),
    sseFrame({
      type: "sources",
      sources: [
        {
          label: "S1",
          documentId: "prd-combat-aoo",
          fullPath: "pathfinder-1e/rules/combat/attacks-of-opportunity.md",
          title: "借机攻击",
        },
      ],
    }),
    sseFrame({ type: "done" }),
  ];
}

export class MockTransport implements GatewayTransport {
  async request(req: TransportRequest): Promise<TransportResponse> {
    throwIfAborted(req.signal);
    if (req.path === "/v1/sessions") {
      const expiresAt = new Date(Date.now() + 30 * 60 * 1000).toISOString();
      return {
        status: 200,
        text: JSON.stringify({ data: { sessionToken: "mock-session-token", expiresAt } }),
      };
    }
    if (req.path === "/v1/session") {
      // DELETE：best-effort 删除会话。
      return { status: 204, text: "" };
    }
    return { status: 404, text: JSON.stringify({ error: { code: "not_found", message: "mock: 未知路径" } }) };
  }

  async stream(req: TransportRequest): Promise<TransportStreamResponse> {
    if (req.path !== "/v1/turns") {
      return {
        status: 404,
        contentType: "application/json",
        body: (async function* () {})(),
        readText: async () => JSON.stringify({ error: { code: "not_found", message: "mock: 未知流路径" } }),
      };
    }

    const frames = buildScript();
    const signal = req.signal;
    const body = (async function* () {
      // 进入流之前先检查一次，取消可最早生效。
      throwIfAborted(signal);
      for (const frame of frames) {
        // 每次 yield 前：可中断延时 + 二次检查，取消时立即抛 aborted，不再继续产出。
        await abortableDelay(60, signal);
        throwIfAborted(signal);
        yield frame;
      }
    })();

    return {
      status: 200,
      contentType: "text/event-stream",
      body,
      readText: async () => "",
    };
  }
}
