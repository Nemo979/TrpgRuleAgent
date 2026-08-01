import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { loadBootstrap, loadSource, login, streamChat } from "./api";
import {
  createConversationForLibrary,
  createSourceSelection,
  isSameSourceRequest,
} from "./conversation";
import type { SourceSelection } from "./conversation";
import {
  exportConversation,
  loadConversations,
  reconcileConversationModels,
  saveConversations,
} from "./storage";
import type { Conversation, LibraryOption, Message, ModelOption, Source } from "./types";

const id = () => crypto.randomUUID();

export function App() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [libraries, setLibraries] = useState<LibraryOption[]>([]);
  const [conversations, setConversations] = useState(loadConversations);
  const [activeId, setActiveId] = useState<string | null>(conversations[0]?.id ?? null);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [libraryPickerOpen, setLibraryPickerOpen] = useState(false);
  const [selectedSource, setSelectedSource] = useState<SourceSelection | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const active = conversations.find((item) => item.id === activeId) ?? null;
  const activeLibrary = libraries.find((item) => item.id === active?.libraryId);

  useEffect(() => {
    void refreshBootstrap();
  }, []);

  useEffect(() => {
    saveConversations(conversations);
  }, [conversations]);

  async function refreshBootstrap() {
    try {
      const data = await loadBootstrap();
      setModels(data.models);
      setLibraries(data.libraries);
      setConversations((items) => reconcileConversationModels(items, data.models));
      setAuthenticated(true);
    } catch (error) {
      setAuthenticated(error instanceof Error && error.message === "unauthorized" ? false : null);
    }
  }

  function createConversation(libraryId: string) {
    if (busy) return;
    const value = createConversationForLibrary(libraries, models, libraryId, {
      id: id(),
      now: new Date().toISOString(),
    });
    if (!value) return;
    setConversations((items) => [value, ...items]);
    setActiveId(value.id);
    setSelectedSource(null);
    setLibraryPickerOpen(false);
  }

  function activateConversation(conversationId: string) {
    if (busy || conversationId === activeId) return;
    setSelectedSource(null);
    setActiveId(conversationId);
  }

  function updateActive(transform: (value: Conversation) => Conversation) {
    if (!activeId) return;
    setConversations((items) =>
      items.map((item) => (item.id === activeId ? transform(item) : item)),
    );
  }

  function deleteActive() {
    if (!active || !window.confirm(`删除“${active.title}”？此操作只影响当前浏览器。`)) return;
    const remaining = conversations.filter((item) => item.id !== active.id);
    setConversations(remaining);
    setSelectedSource(null);
    setActiveId(remaining[0]?.id ?? null);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const text = input.trim();
    if (!text || !active || busy) return;
    setInput("");
    const userMessage: Message = {
      id: id(),
      role: "user",
      content: text,
      createdAt: new Date().toISOString(),
    };
    const assistantId = id();
    const modelLabel = models.find((item) => item.id === active.modelId)?.label ?? active.modelId;
    const pending: Conversation = {
      ...active,
      title: active.messages.length === 0 ? text.slice(0, 28) : active.title,
      messages: [
        ...active.messages,
        userMessage,
        {
          id: assistantId,
          role: "assistant",
          content: "",
          modelLabel,
          createdAt: new Date().toISOString(),
        },
      ],
      updatedAt: new Date().toISOString(),
    };
    setConversations((items) => items.map((item) => (item.id === active.id ? pending : item)));
    await generateAnswer(pending, assistantId);
  }

  async function regenerate() {
    if (!active || busy || active.messages.at(-1)?.role !== "assistant") return;
    const assistantId = id();
    const modelLabel = models.find((item) => item.id === active.modelId)?.label ?? active.modelId;
    const pending: Conversation = {
      ...active,
      messages: [
        ...active.messages.slice(0, -1),
        {
          id: assistantId,
          role: "assistant",
          content: "",
          modelLabel,
          createdAt: new Date().toISOString(),
        },
      ],
      updatedAt: new Date().toISOString(),
    };
    setConversations((items) => items.map((item) => (item.id === active.id ? pending : item)));
    await generateAnswer(pending, assistantId);
  }

  async function generateAnswer(pending: Conversation, assistantId: string) {
    setBusy(true);
    setStatus("正在思考");
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      for await (const chatEvent of streamChat(pending, controller.signal)) {
        if (chatEvent.type === "status") {
          setStatus(
            chatEvent.status === "searching"
              ? "正在检索规则"
              : chatEvent.status === "reading"
                ? "正在核对原文"
                : chatEvent.status === "answering"
                  ? "正在组织回答"
                  : "正在思考",
          );
        } else if (chatEvent.type === "context_truncated") {
          setConversations((items) =>
            setContextTruncation(
              items,
              pending.id,
              assistantId,
              chatEvent.droppedMessages,
            ),
          );
        } else if (chatEvent.type === "text_delta") {
          setConversations((items) =>
            appendAssistant(items, pending.id, assistantId, chatEvent.delta),
          );
        } else if (chatEvent.type === "sources") {
          setConversations((items) =>
            setAssistantSources(items, pending.id, assistantId, chatEvent.sources),
          );
        } else if (chatEvent.type === "error") {
          setConversations((items) =>
            appendAssistant(items, pending.id, assistantId, `\n\n${chatEvent.message}`),
          );
        }
      }
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setConversations((items) =>
          appendAssistant(
            items,
            pending.id,
            assistantId,
            `\n\n${error instanceof Error ? error.message : "回答生成失败，请稍后重试。"}`,
          ),
        );
      }
    } finally {
      abortRef.current = null;
      setBusy(false);
      setStatus("");
    }
  }

  async function showSource(source: Source) {
    if (!active) return;
    const request = createSourceSelection(active, source, id());
    setSelectedSource(request);
    try {
      const document = await loadSource(request.libraryId, source.documentId);
      setSelectedSource((current) =>
        isSameSourceRequest(current, request)
          ? { ...request, content: document.content }
          : current,
      );
    } catch (error) {
      setSelectedSource((current) =>
        isSameSourceRequest(current, request)
          ? {
              ...request,
              error: error instanceof Error ? error.message : "无法读取来源",
            }
          : current,
      );
    }
  }

  if (authenticated === false) {
    return <Login onSuccess={refreshBootstrap} />;
  }
  if (authenticated !== true) {
    return <div className="center-card">正在连接规则藏书阁……</div>;
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">R</span>
          <div>
            <strong>规则藏书阁</strong>
            <small>Evidence-first rules</small>
          </div>
        </div>
        <button
          className="primary"
          disabled={busy}
          onClick={() => setLibraryPickerOpen(true)}
        >
          ＋ 新对话
        </button>
        <nav className="conversation-list">
          {conversations.map((conversation) => (
            <button
              className={conversation.id === activeId ? "conversation active" : "conversation"}
              disabled={busy}
              key={conversation.id}
              onClick={() => activateConversation(conversation.id)}
            >
              <span>{conversation.title}</span>
              <small>
                {libraries.find((item) => item.id === conversation.libraryId)?.name ??
                  conversation.libraryId}
              </small>
            </button>
          ))}
        </nav>
      </aside>

      <main className="chat">
        {!active ? (
          <Welcome libraries={libraries} onCreate={createConversation} />
        ) : (
          <>
            <header className="toolbar">
              <div>
                <strong>{active.title}</strong>
                <span>{activeLibrary?.name}</span>
              </div>
              <div className="toolbar-actions">
                <button
                  className="ghost mobile-only"
                  disabled={busy}
                  onClick={() => setLibraryPickerOpen(true)}
                >
                  新建
                </button>
                <select
                  className="mobile-only"
                  aria-label="切换对话"
                  value={active.id}
                  disabled={busy}
                  onChange={(event) => activateConversation(event.target.value)}
                >
                  {conversations.map((conversation) => (
                    <option key={conversation.id} value={conversation.id}>
                      {conversation.title}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="选择模型"
                  value={active.modelId}
                  disabled={busy}
                  onChange={(event) =>
                    updateActive((value) => ({ ...value, modelId: event.target.value }))
                  }
                >
                  {models.map((model) => (
                    <option value={model.id} key={model.id}>
                      {model.label}
                    </option>
                  ))}
                </select>
                <button className="ghost" onClick={() => exportConversation(active)}>
                  导出
                </button>
                <button
                  className="ghost desktop-only"
                  disabled={busy || active.messages.at(-1)?.role !== "assistant"}
                  onClick={() => void regenerate()}
                >
                  重新生成
                </button>
                <button
                  className="ghost desktop-only"
                  disabled={busy}
                  onClick={deleteActive}
                >
                  删除
                </button>
                <details className="mobile-actions mobile-only">
                  <summary>操作</summary>
                  <div>
                    <button type="button" onClick={() => exportConversation(active)}>
                      导出
                    </button>
                    <button
                      type="button"
                      disabled={busy || active.messages.at(-1)?.role !== "assistant"}
                      onClick={() => void regenerate()}
                    >
                      重新生成
                    </button>
                    <button type="button" disabled={busy} onClick={deleteActive}>
                      删除
                    </button>
                  </div>
                </details>
              </div>
            </header>
            {activeLibrary && active.libraryRevision !== activeLibrary.revision && (
              <div className="revision-warning">规则库已经更新，旧回答的来源可能失效。</div>
            )}
            <section className="messages">
              {active.messages.length === 0 && (
                <div className="empty-state">
                  <span>已绑定规则库</span>
                  <h1>{activeLibrary?.name}</h1>
                  <p>规则结论会检索并核对当前规则库；证据不足时不会用模型记忆补全。</p>
                </div>
              )}
              {active.messages.map((message) => (
                <MessageView message={message} key={message.id} onSource={showSource} />
              ))}
              {status && <div className="status"><i />{status}</div>}
            </section>
            <form className="composer" onSubmit={submit}>
              <textarea
                value={input}
                rows={2}
                placeholder={`询问 ${activeLibrary?.name ?? "当前规则库"}……`}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
              />
              {busy ? (
                <button type="button" className="stop" onClick={() => abortRef.current?.abort()}>
                  停止
                </button>
              ) : (
                <button type="submit" className="send" disabled={!input.trim()}>
                  发送
                </button>
              )}
            </form>
          </>
        )}
      </main>
      {selectedSource && (
        <SourceDrawer value={selectedSource} onClose={() => setSelectedSource(null)} />
      )}
      {libraryPickerOpen && (
        <LibraryPicker
          libraries={libraries}
          onClose={() => setLibraryPickerOpen(false)}
          onSelect={createConversation}
        />
      )}
    </div>
  );
}

function Login({ onSuccess }: { onSuccess: () => Promise<void> }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  return (
    <main className="login-page">
      <form
        className="login-card"
        onSubmit={async (event) => {
          event.preventDefault();
          setError("");
          try {
            await login(password);
            await onSuccess();
          } catch (reason) {
            setError(reason instanceof Error ? reason.message : "登录失败");
          }
        }}
      >
        <span className="brand-mark large">R</span>
        <p className="eyebrow">PRIVATE RULE LIBRARY</p>
        <h1>进入规则藏书阁</h1>
        <p>使用管理员提供的共享密码访问。</p>
        <input
          type="password"
          autoFocus
          value={password}
          placeholder="共享密码"
          onChange={(event) => setPassword(event.target.value)}
        />
        {error && <div className="form-error">{error}</div>}
        <button className="primary" type="submit">进入</button>
      </form>
    </main>
  );
}

function Welcome({
  libraries,
  onCreate,
}: {
  libraries: LibraryOption[];
  onCreate: (id: string) => void;
}) {
  return (
    <section className="welcome">
      <p className="eyebrow">SELECT A RULE LIBRARY</p>
      <h1>从哪套规则开始？</h1>
      <p>每个对话只绑定一个游戏系统与版本，避免不同规则互相污染。</p>
      <LibraryGrid libraries={libraries} onSelect={onCreate} />
    </section>
  );
}

function LibraryPicker({
  libraries,
  onClose,
  onSelect,
}: {
  libraries: LibraryOption[];
  onClose: () => void;
  onSelect: (id: string) => void;
}) {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div
      className="library-picker-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="library-picker"
        role="dialog"
        aria-modal="true"
        aria-labelledby="library-picker-title"
      >
        <header>
          <div>
            <p className="eyebrow">SELECT A RULE LIBRARY</p>
            <h2 id="library-picker-title">选择新对话的规则库</h2>
            <p>创建后将固定绑定所选游戏系统与版本。</p>
          </div>
          <button type="button" aria-label="关闭规则库选择" onClick={onClose}>
            ×
          </button>
        </header>
        <LibraryGrid libraries={libraries} onSelect={onSelect} autoFocus />
      </section>
    </div>
  );
}

function LibraryGrid({
  libraries,
  onSelect,
  autoFocus = false,
}: {
  libraries: LibraryOption[];
  onSelect: (id: string) => void;
  autoFocus?: boolean;
}) {
  if (libraries.length === 0) {
    return <p className="library-empty">暂无可用规则库。</p>;
  }

  return (
    <div className="library-grid">
      {libraries.map((library, index) => (
        <button
          type="button"
          key={library.id}
          autoFocus={autoFocus && index === 0}
          onClick={() => onSelect(library.id)}
        >
          <span>{library.system}</span>
          <strong>{library.name}</strong>
          <small>{library.edition}</small>
        </button>
      ))}
    </div>
  );
}

function MessageView({
  message,
  onSource,
}: {
  message: Message;
  onSource: (source: Source) => void;
}) {
  const decorated = useMemo(() => decorateCitations(message.content, message.sources ?? []), [message]);
  return (
    <article className={`message ${message.role}`}>
      <div className="message-label">{message.role === "user" ? "你" : message.modelLabel ?? "助手"}</div>
      <div className="message-body">
        {!!message.droppedContextMessages && (
          <div className="context-note">
            较早的 {message.droppedContextMessages} 条消息未发送给模型。
          </div>
        )}
        {message.role === "assistant" ? (
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            urlTransform={(url) => (url.startsWith("source:") ? url : url)}
            components={{
              a: ({ href, children }) =>
                href?.startsWith("source:") ? (
                  <button
                    className="citation"
                    onClick={() => {
                      const source = message.sources?.find(
                        (item) => item.label === href.slice("source:".length),
                      );
                      if (source) onSource(source);
                    }}
                  >
                    {children}
                  </button>
                ) : (
                  <a href={href} target="_blank" rel="noreferrer">{children}</a>
                ),
            }}
          >
            {decorated}
          </ReactMarkdown>
        ) : (
          <p>{message.content}</p>
        )}
        {!!message.sources?.length && (
          <div className="source-row">
            {message.sources.map((source, index) => (
              <button key={source.label} onClick={() => onSource(source)}>
                {index + 1}. {source.title}
              </button>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}

function SourceDrawer({
  value,
  onClose,
}: {
  value: { source: Source; content?: string; error?: string };
  onClose: () => void;
}) {
  return (
    <aside className="source-drawer">
      <header>
        <div>
          <span>来源</span>
          <strong>{value.source.title}</strong>
          <small>{value.source.fullPath}</small>
        </div>
        <button onClick={onClose}>×</button>
      </header>
      <div className="source-content">
        {value.error ?? value.content ?? "正在读取原文……"}
      </div>
    </aside>
  );
}

function decorateCitations(content: string, sources: Source[]): string {
  const indexes = new Map(sources.map((source, index) => [source.label, index + 1]));
  return content.replace(/\[(S\d+)]/g, (_, label: string) => {
    const index = indexes.get(label);
    return index ? `[${index}](source:${label})` : "";
  });
}

function appendAssistant(
  conversations: Conversation[],
  conversationId: string,
  messageId: string,
  delta: string,
): Conversation[] {
  return conversations.map((conversation) =>
    conversation.id === conversationId
      ? {
          ...conversation,
          messages: conversation.messages.map((message) =>
            message.id === messageId ? { ...message, content: message.content + delta } : message,
          ),
          updatedAt: new Date().toISOString(),
        }
      : conversation,
  );
}

function setAssistantSources(
  conversations: Conversation[],
  conversationId: string,
  messageId: string,
  sources: Source[],
): Conversation[] {
  return conversations.map((conversation) =>
    conversation.id === conversationId
      ? {
          ...conversation,
          messages: conversation.messages.map((message) =>
            message.id === messageId ? { ...message, sources } : message,
          ),
        }
      : conversation,
  );
}

function setContextTruncation(
  conversations: Conversation[],
  conversationId: string,
  messageId: string,
  droppedMessages: number,
): Conversation[] {
  return conversations.map((conversation) =>
    conversation.id === conversationId
      ? {
          ...conversation,
          messages: conversation.messages.map((message) =>
            message.id === messageId
              ? { ...message, droppedContextMessages: droppedMessages }
              : message,
          ),
        }
      : conversation,
  );
}
