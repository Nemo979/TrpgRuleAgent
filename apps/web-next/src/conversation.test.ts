import { describe, expect, it } from "vitest";
import {
  createConversationForLibrary,
  createSourceSelection,
  isSameSourceRequest,
} from "./conversation";
import type {
  Conversation,
  LibraryOption,
  ModelOption,
  Source,
} from "./types";

const libraries: LibraryOption[] = [
  {
    id: "pathfinder-1e",
    name: "Pathfinder 1E",
    system: "Pathfinder",
    edition: "1E",
    revision: "pf-revision",
  },
  {
    id: "xi-yao-wan-yao-1.2",
    name: "夕妖晚谣",
    system: "夕妖晚谣",
    edition: "1.2",
    revision: "xywy-revision",
  },
];

const models: ModelOption[] = [
  { id: "shared-model", label: "共享模型", contextWindow: 128_000 },
];

const identity = {
  id: "conversation-2",
  now: "2026-07-30T00:00:00.000Z",
};

describe("createConversationForLibrary", () => {
  it("binds the explicitly selected library instead of the first library", () => {
    expect(
      createConversationForLibrary(libraries, models, "xi-yao-wan-yao-1.2", identity),
    ).toMatchObject({
      id: "conversation-2",
      libraryId: "xi-yao-wan-yao-1.2",
      libraryRevision: "xywy-revision",
      modelId: "shared-model",
    });
  });

  it("does not fall back to the first library for an invalid selection", () => {
    expect(
      createConversationForLibrary(libraries, models, "missing-library", identity),
    ).toBeNull();
  });

  it("does not create a conversation when no model is available", () => {
    expect(
      createConversationForLibrary(libraries, [], "pathfinder-1e", identity),
    ).toBeNull();
  });
});

describe("conversation-bound source requests", () => {
  const source: Source = {
    label: "S1",
    documentId: "pathfinder-1e:combat",
    title: "借机攻击",
    fullPath: "核心规则 > 战斗 > 借机攻击",
    metadata: {},
  };
  const conversation: Pick<Conversation, "id" | "libraryId"> = {
    id: "pf-conversation",
    libraryId: "pathfinder-1e",
  };

  it("accepts only the same request token, conversation, and library", () => {
    const request = createSourceSelection(conversation, source, "request-1");

    expect(isSameSourceRequest(request, request)).toBe(true);
    expect(
      isSameSourceRequest(
        { ...request, requestToken: "request-2" },
        request,
      ),
    ).toBe(false);
    expect(
      isSameSourceRequest(
        { ...request, conversationId: "other-conversation" },
        request,
      ),
    ).toBe(false);
    expect(
      isSameSourceRequest(
        { ...request, libraryId: "golden-sky-stories-zh-1-2" },
        request,
      ),
    ).toBe(false);
    expect(isSameSourceRequest(null, request)).toBe(false);
  });
});
