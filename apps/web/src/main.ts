/**
 * 应用入口（apps/web/src/main.ts）。
 *
 * 装配：传输层 → GatewayClient → App（凭据/生命周期）→ ChatView → 状态机。
 *
 * 安全要点：
 * - 默认使用 BrowserTransport（同源相对路径），走真实网络；
 * - mock 传输仅在【开发模式 + ?mock=1】双条件下加载：
 *   `import.meta.env.DEV` 在生产构建被静态替换为 false，整个分支
 *   （含动态 import("./mock-transport.ts")）会被 tree-shake 掉，
 *   生产 bundle 不含任何 mock 代码 / token / fixture；
 * - API Key 仅存于 App 实例私有内存，刷新即丢失；
 *   绝不写入 localStorage / sessionStorage / cookie / dataset / URL；
 * - 新会话/断开会清空内存凭据；重复创建会话前 best-effort 清理旧会话。
 */

import {
  BrowserTransport,
  GatewayClient,
  type GatewayTransport,
} from "@trpg-rule-agent/gateway-client";
import { App } from "./app.ts";
import type { ChatClient } from "./controller.ts";
import { createInitialState, reducer, type Action } from "./state.ts";
import type { SessionCreateOptions } from "./types.ts";
import { ChatView } from "./view.ts";

const root = document.getElementById("app");
if (!root) {
  throw new Error("缺少 #app 容器");
}

/**
 * 构造传输层。mock 只在开发模式下可用（生产构建整个分支被移除）。
 */
async function createTransport(gatewayBaseUrl: string): Promise<GatewayTransport> {
  if (
    import.meta.env.DEV &&
    new URLSearchParams(window.location.search).get("mock") === "1"
  ) {
    const { MockTransport } = await import("./mock-transport.ts");
    return new MockTransport();
  }
  return new BrowserTransport({ baseUrl: gatewayBaseUrl });
}

async function createClient(gatewayBaseUrl: string): Promise<ChatClient> {
  const transport = await createTransport(gatewayBaseUrl);
  return new GatewayClient({ transport });
}

let state = createInitialState();

function dispatch(action: Action): void {
  state = reducer(state, action);
  view.render(state);
}

const app = new App({ createClient, dispatch });

const view = new ChatView(root, {
  onCreateSession(values) {
    const options: SessionCreateOptions = {
      provider: values.provider || "openai-compatible",
      model: { id: values.modelId || "gpt-4o-mini" },
      baseUrl: values.baseUrl,
      ...(values.ruleset.length > 0 ? { rulesetId: values.ruleset } : {}),
    };

    void app.createSession(values.gatewayUrl.trim(), values.apiKey, options).then((ok) => {
      if (ok) {
        // 创建成功后清空输入框（key 已移入 App 私有内存）。
        view.clearApiKeyInput();
        view.focusChatInput();
      }
      // 失败：保留输入框内容以便重试（错误已由 controller 广播）。
    });
  },

  onSend(input) {
    void app.sendTurn(input);
  },

  onStop() {
    app.stop();
  },

  onNewSession() {
    // 清空内存凭据 + best-effort 删除服务端会话 + 重置上下文。
    void app.newSession();
  },
});

view.render(state);
