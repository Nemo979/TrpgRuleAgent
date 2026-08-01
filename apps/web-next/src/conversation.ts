import type {
  Conversation,
  LibraryOption,
  ModelOption,
  Source,
} from "./types";

interface ConversationIdentity {
  id: string;
  now: string;
}

export interface SourceSelection {
  requestToken: string;
  conversationId: string;
  libraryId: string;
  source: Source;
  content?: string;
  error?: string;
}

export function createConversationForLibrary(
  libraries: LibraryOption[],
  models: ModelOption[],
  libraryId: string,
  identity: ConversationIdentity,
): Conversation | null {
  const library = libraries.find((item) => item.id === libraryId);
  const model = models[0];
  if (!library || !model) return null;

  return {
    id: identity.id,
    title: "新对话",
    libraryId: library.id,
    libraryRevision: library.revision,
    modelId: model.id,
    messages: [],
    updatedAt: identity.now,
  };
}

export function createSourceSelection(
  conversation: Pick<Conversation, "id" | "libraryId">,
  source: Source,
  requestToken: string,
): SourceSelection {
  return {
    requestToken,
    conversationId: conversation.id,
    libraryId: conversation.libraryId,
    source,
  };
}

export function isSameSourceRequest(
  current: SourceSelection | null,
  request: SourceSelection,
): boolean {
  return (
    current?.requestToken === request.requestToken
    && current.conversationId === request.conversationId
    && current.libraryId === request.libraryId
  );
}
