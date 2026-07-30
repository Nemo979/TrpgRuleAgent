import type { Conversation, ModelOption } from "./types";

const STORAGE_KEY = "trpg-rule-app.conversations.v1";

export function loadConversations(): Conversation[] {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

export function saveConversations(values: Conversation[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(values));
}

export function reconcileConversationModels(
  conversations: Conversation[],
  models: ModelOption[],
): Conversation[] {
  const fallback = models[0]?.id;
  if (!fallback) return conversations;
  const available = new Set(models.map((model) => model.id));
  return conversations.map((conversation) =>
    available.has(conversation.modelId)
      ? conversation
      : { ...conversation, modelId: fallback },
  );
}

export function exportConversation(value: Conversation): void {
  const lines = [`# ${value.title}`, "", `规则库：${value.libraryId}`, ""];
  for (const message of value.messages) {
    lines.push(message.role === "user" ? "## 用户" : "## 助手", "", message.content, "");
    if (message.modelLabel) lines.push(`模型：${message.modelLabel}`, "");
    if (message.sources?.length) {
      lines.push("来源：", "");
      for (const [index, source] of message.sources.entries()) {
        lines.push(`${index + 1}. ${source.fullPath}`);
      }
      lines.push("");
    }
  }
  const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${value.title.replace(/[\\/:*?"<>|]/g, "-")}.md`;
  link.click();
  URL.revokeObjectURL(url);
}
