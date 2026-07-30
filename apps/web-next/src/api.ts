import type { ChatEvent, Conversation, LibraryOption, ModelOption } from "./types";

export async function login(password: string): Promise<void> {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!response.ok) throw new Error("密码不正确");
}

export async function loadBootstrap(): Promise<{
  models: ModelOption[];
  libraries: LibraryOption[];
}> {
  const response = await fetch("/api/bootstrap");
  if (!response.ok) throw new Error(response.status === 401 ? "unauthorized" : "加载配置失败");
  return response.json();
}

export async function* streamChat(
  conversation: Conversation,
  signal: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      model_id: conversation.modelId,
      library_id: conversation.libraryId,
      messages: conversation.messages
        .filter(({ content }) => content.trim().length > 0)
        .map(({ role, content }) => ({ role, content })),
    }),
    signal,
  });
  if (!response.ok || !response.body) {
    let detail = "";
    try {
      detail = String((await response.json() as { detail?: unknown }).detail ?? "");
    } catch {
      // The response may be an empty proxy error page.
    }
    if (response.status === 404 && detail === "model not found") {
      throw new Error("当前对话使用的模型已下线，已为你切换到可用模型，请重试。");
    }
    if (response.status === 404 && detail === "library not found") {
      throw new Error("当前对话绑定的规则库已经下线，无法继续回答。");
    }
    throw new Error(`无法开始回答（HTTP ${response.status}）。`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const data = frame
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (data) yield JSON.parse(data) as ChatEvent;
    }
    if (done) return;
  }
}

export async function loadSource(libraryId: string, documentId: string): Promise<{
  title: string;
  fullPath: string;
  content: string;
  metadata: Record<string, unknown>;
}> {
  const response = await fetch(
    `/api/libraries/${encodeURIComponent(libraryId)}/documents/${encodeURIComponent(documentId)}`,
  );
  if (!response.ok) throw new Error("来源已失效或规则库已经更新");
  return response.json();
}
