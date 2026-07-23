/**
 * DOM 渲染层（apps/web/src/view.ts）。
 *
 * 安全红线：
 * - 严禁使用 innerHTML / outerHTML / insertAdjacentHTML；
 * - 严禁把模型输出当 HTML 渲染；一律用 textContent / createTextNode；
 * - 工具时间线只展示 toolName 与状态（start/end/error），绝不展示 args / results；
 * - 来源卡片只展示 label / title / fullPath / documentId 文本字段。
 * 可访问性：所有输入有 <label for>，流式区与状态用 aria-live="polite"。
 */

import { canCreateSession, canSend, type ChatState } from "./state.ts";

export interface SessionFormValues {
  gatewayUrl: string;
  provider: string;
  modelId: string;
  baseUrl: string;
  apiKey: string;
  ruleset: string;
}

export interface ViewHandlers {
  onCreateSession: (values: SessionFormValues) => void;
  onSend: (input: string) => void;
  onStop: () => void;
  onNewSession: () => void;
}

interface ElOpts {
  class?: string;
  text?: string;
  attrs?: Record<string, string>;
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  opts: ElOpts = {},
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (opts.class !== undefined) {
    node.className = opts.class;
  }
  if (opts.text !== undefined) {
    // 安全：文本节点，绝不解析为 HTML。
    node.textContent = opts.text;
  }
  if (opts.attrs !== undefined) {
    for (const [k, v] of Object.entries(opts.attrs)) {
      node.setAttribute(k, v);
    }
  }
  return node;
}

const STATUS_TEXT: Record<ChatState["connection"], string> = {
  disconnected: "未连接",
  connecting: "连接中…",
  connected: "已连接",
  error: "连接异常",
};

export class ChatView {
  readonly #handlers: ViewHandlers;

  // 连接表单元素（main.ts 需要读取 / 清空 apiKey）。
  readonly #apiKeyInput: HTMLInputElement;
  readonly #gatewayUrlInput: HTMLInputElement;
  readonly #providerInput: HTMLInputElement;
  readonly #modelIdInput: HTMLInputElement;
  readonly #baseUrlInput: HTMLInputElement;
  readonly #rulesetInput: HTMLInputElement;

  // 状态 / 聊天区元素。
  readonly #statusEl: HTMLElement;
  readonly #messagesEl: HTMLElement;
  readonly #timelineEl: HTMLElement;
  readonly #sourcesEl: HTMLElement;
  readonly #errorEl: HTMLElement;
  readonly #chatInput: HTMLTextAreaElement;
  readonly #sendBtn: HTMLButtonElement;
  readonly #stopBtn: HTMLButtonElement;
  readonly #newSessionBtn: HTMLButtonElement;
  readonly #createBtn: HTMLButtonElement;

  /** 已渲染消息节点缓存（按 id），用于增量更新，避免整段重绘。 */
  readonly #messageNodes = new Map<string, HTMLElement>();
  readonly #STREAMING_ID = "__streaming__";

  constructor(root: HTMLElement, handlers: ViewHandlers) {
    this.#handlers = handlers;

    const layout = el("div", { class: "layout" });

    // —— 连接面板 ——
    const connectPanel = el("section", { class: "panel connect-panel" });
    connectPanel.append(el("h1", { text: "TRPG 规则 Agent · 调试台" }));

    const connectForm = el("form", { attrs: { id: "connect-form", autocomplete: "off" } });

    this.#gatewayUrlInput = el("input", {
      attrs: { id: "gw-url", type: "text", placeholder: "同源（留空）" },
    });
    this.#providerInput = el("input", {
      attrs: { id: "provider", type: "text", value: "openai-compatible" },
    });
    this.#modelIdInput = el("input", {
      attrs: { id: "model-id", type: "text", placeholder: "如 gpt-4o-mini" },
    });
    this.#baseUrlInput = el("input", {
      attrs: { id: "base-url", type: "text", placeholder: "https://api.openai.com/v1" },
    });
    this.#apiKeyInput = el("input", {
      attrs: { id: "api-key", type: "password", placeholder: "模型 API Key（仅本次会话内存）" },
    });
    this.#rulesetInput = el("input", {
      attrs: { id: "ruleset", type: "text", placeholder: "可选，如 pathfinder-1e" },
    });

    const toggleKey = el("button", { text: "显示", attrs: { type: "button", id: "toggle-key" } });
    toggleKey.addEventListener("click", () => {
      const showing = this.#apiKeyInput.type === "text";
      this.#apiKeyInput.type = showing ? "password" : "text";
      toggleKey.textContent = showing ? "显示" : "隐藏";
      this.#apiKeyInput.focus();
    });

    const keyRow = el("div", { class: "key-row" });
    keyRow.append(this.#apiKeyInput, toggleKey);

    this.#createBtn = el("button", {
      text: "创建会话",
      class: "primary",
      attrs: { type: "submit", id: "create-session" },
    });
    const createBtn = this.#createBtn;

    connectForm.append(
      el("label", { text: "Gateway URL", attrs: { for: "gw-url" } }),
      this.#gatewayUrlInput,
      el("label", { text: "Provider", attrs: { for: "provider" } }),
      this.#providerInput,
      el("label", { text: "模型 ID", attrs: { for: "model-id" } }),
      this.#modelIdInput,
      el("label", { text: "Base URL", attrs: { for: "base-url" } }),
      this.#baseUrlInput,
      el("label", { text: "API Key", attrs: { for: "api-key" } }),
      keyRow,
      el("label", { text: "ruleset（可选）", attrs: { for: "ruleset" } }),
      this.#rulesetInput,
      createBtn,
    );

    this.#statusEl = el("div", { class: "status", attrs: { id: "connection-status", "aria-live": "polite" } });

    connectForm.addEventListener("submit", (e) => {
      e.preventDefault();
      // 连接中禁止重复提交（Enter 提交同样拦截）。
      if (this.#createBtn.disabled) {
        return;
      }
      this.#handlers.onCreateSession(this.readSessionForm());
    });

    connectPanel.append(connectForm, this.#statusEl);

    // —— 聊天面板 ——
    const chatPanel = el("section", { class: "panel chat-panel" });

    this.#messagesEl = el("div", { attrs: { id: "messages", "aria-live": "polite" } });
    this.#timelineEl = el("div", {
      class: "timeline",
      attrs: { id: "tool-timeline", "aria-live": "polite" },
    });
    this.#sourcesEl = el("div", { class: "sources", attrs: { id: "sources" } });
    this.#errorEl = el("div", { class: "error-box", attrs: { id: "error", role: "alert" } });
    this.#errorEl.style.display = "none";

    const chatForm = el("form", { attrs: { id: "chat-form", autocomplete: "off" } });
    this.#chatInput = el("textarea", {
      attrs: { id: "chat-input", placeholder: "输入消息… Enter 发送，Shift+Enter 换行" },
    });
    const chatLabel = el("label", {
      text: "输入消息",
      class: "visually-hidden",
      attrs: { for: "chat-input" },
    });
    this.#sendBtn = el("button", { text: "发送", class: "primary", attrs: { type: "submit", id: "send" } });
    const sendBtn = this.#sendBtn;
    this.#stopBtn = el("button", { text: "停止生成", attrs: { type: "button", id: "stop" } });
    this.#newSessionBtn = el("button", { text: "新会话/断开", attrs: { type: "button", id: "new-session" } });

    chatForm.append(chatLabel, this.#chatInput, sendBtn, this.#stopBtn, this.#newSessionBtn);

    chatForm.addEventListener("submit", (e) => {
      e.preventDefault();
      // Enter 快捷键会绕过禁用按钮触发 submit，这里同样按可发送状态拦截。
      if (this.#sendBtn.disabled) {
        return;
      }
      const value = this.#chatInput.value;
      if (value.trim().length === 0) {
        return;
      }
      this.#handlers.onSend(value);
      this.#chatInput.value = "";
      this.#chatInput.focus();
    });

    this.#chatInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        chatForm.requestSubmit();
      }
    });

    this.#stopBtn.addEventListener("click", () => this.#handlers.onStop());
    this.#newSessionBtn.addEventListener("click", () => this.#handlers.onNewSession());

    chatPanel.append(
      this.#messagesEl,
      this.#timelineEl,
      this.#sourcesEl,
      this.#errorEl,
      chatForm,
    );

    layout.append(connectPanel, chatPanel);
    root.append(layout);
  }

  /** 读取连接表单当前值（不清除 apiKey；清除由 main.ts 显式控制）。 */
  readSessionForm(): SessionFormValues {
    return {
      gatewayUrl: this.#gatewayUrlInput.value.trim(),
      provider: this.#providerInput.value.trim(),
      modelId: this.#modelIdInput.value.trim(),
      baseUrl: this.#baseUrlInput.value.trim(),
      apiKey: this.#apiKeyInput.value,
      ruleset: this.#rulesetInput.value.trim(),
    };
  }

  /** 创建会话成功后清空 API Key 输入框（凭据已从输入框移除，仅留内存变量）。 */
  clearApiKeyInput(): void {
    this.#apiKeyInput.value = "";
  }

  focusChatInput(): void {
    this.#chatInput.focus();
  }

  render(state: ChatState): void {
    // 连接状态
    this.#statusEl.textContent = STATUS_TEXT[state.connection];
    this.#statusEl.className = `status ${state.connection === "connected" ? "connected" : ""}${
      state.connection === "error" ? " error" : ""
    }`;

    // 消息 + 流式缓冲（流式文本作为临时 assistant 条目，生成结束后由 reducer 固化）
    const streamingEntry =
      state.generating && state.streamingText.length > 0
        ? [{ role: "assistant" as const, id: this.#STREAMING_ID, content: state.streamingText }]
        : [];
    const allMessages = [...state.messages, ...streamingEntry];
    for (const m of allMessages) {
      let node = this.#messageNodes.get(m.id);
      if (!node) {
        node = el("div", { class: `msg ${m.role}` });
        node.append(el("span", { class: "role", text: m.role === "user" ? "你" : "助手" }));
        const body = el("div", { class: "body" });
        node.append(body);
        this.#messageNodes.set(m.id, node);
        this.#messagesEl.append(node);
      }
      // 安全：只更新文本节点内容。
      const body = node.querySelector(".body");
      if (body) {
        body.textContent = m.content;
      }
    }
    // 生成结束时移除流式临时节点（已固化为正式消息）。
    if (!state.generating) {
      const stale = this.#messageNodes.get(this.#STREAMING_ID);
      if (stale) {
        stale.remove();
        this.#messageNodes.delete(this.#STREAMING_ID);
      }
    }
    this.#messagesEl.scrollTop = this.#messagesEl.scrollHeight;

    // 工具时间线（仅 toolName + 状态）
    this.#timelineEl.replaceChildren();
    for (const t of state.toolTimeline) {
      const row = el("div", { class: "tool" });
      const badge = el("span", { class: `badge ${t.status}`, text: t.status });
      const name = el("span", { text: t.toolName });
      row.append(badge, name);
      if (t.status === "error" && t.error) {
        row.append(el("span", { text: `· ${t.error.category}`, class: "meta" }));
      }
      this.#timelineEl.append(row);
    }

    // 来源卡片
    this.#sourcesEl.replaceChildren();
    for (const s of state.sources) {
      const card = el("div", { class: "source-card" });
      card.append(el("div", { class: "title", text: s.title ?? s.label }));
      card.append(el("div", { class: "meta", text: `label: ${s.label} · id: ${s.documentId}` }));
      card.append(el("div", { class: "meta", text: s.fullPath }));
      this.#sourcesEl.append(card);
    }

    // 错误区
    if (state.error) {
      this.#errorEl.style.display = "";
      const code = state.error.code !== undefined ? `[${state.error.code}] ` : "";
      this.#errorEl.textContent = `${code}${state.error.message}`;
    } else {
      this.#errorEl.style.display = "none";
      this.#errorEl.textContent = "";
    }

    // 按钮可用性（派生逻辑集中在 state.ts 的纯函数，可单测）
    this.#sendBtn.disabled = !canSend(state); // 未连接或生成中禁用
    this.#createBtn.disabled = !canCreateSession(state); // 连接中禁用，防重复提交
    this.#stopBtn.disabled = !state.generating;
    this.#newSessionBtn.disabled = !state.connected;
  }
}
