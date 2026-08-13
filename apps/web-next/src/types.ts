export interface ModelOption {
  id: string;
  label: string;
  contextWindow: number;
}

export interface LibraryOption {
  id: string;
  name: string;
  system: string;
  edition: string;
  revision: string;
}

export interface Source {
  label: string;
  documentId: string;
  title: string;
  fullPath: string;
  metadata: Record<string, unknown>;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  modelLabel?: string;
  sources?: Source[];
  droppedContextMessages?: number;
  createdAt: string;
}

export interface Conversation {
  id: string;
  title: string;
  libraryId: string;
  modelId: string;
  libraryRevision: string;
  messages: Message[];
  updatedAt: string;
}

export type ChatEvent =
  | { type: "status"; status: "thinking" | "searching" | "reading" | "answering" }
  | { type: "context_truncated"; droppedMessages: number }
  | { type: "safe_refusal"; reason: string }
  | { type: "text_delta"; delta: string }
  | { type: "sources"; sources: Source[] }
  | {
      type: "error";
      message: string;
      code?: string;
      requestId?: string;
      retryable?: boolean;
    }
  | { type: "done" };
