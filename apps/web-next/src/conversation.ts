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

// When opening the page, prefer a model whose id contains one of these hints.
// Agnes and SenseNova stay fully selectable in the dropdown; only the default
// changes. Their exclusion is an evaluation-scope decision, not a UI one.
const DEFAULT_MODEL_PREFERENCE = ["mimo"];

function selectDefaultModel(models: ModelOption[]): ModelOption | null {
  for (const keyword of DEFAULT_MODEL_PREFERENCE) {
    const found = models.find((model) => model.id.toLowerCase().includes(keyword));
    if (found) return found;
  }
  return models[0] ?? null;
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
  const model = selectDefaultModel(models);
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
