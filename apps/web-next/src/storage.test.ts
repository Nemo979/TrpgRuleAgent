import { describe, expect, it } from "vitest";
import { reconcileConversationModels } from "./storage";
import type { Conversation, ModelOption } from "./types";

const conversation: Conversation = {
  id: "conversation-1",
  title: "旧对话",
  libraryId: "pathfinder-1e",
  libraryRevision: "revision-1",
  modelId: "local-mock",
  messages: [],
  updatedAt: "2026-07-30T00:00:00.000Z",
};

const models: ModelOption[] = [
  { id: "deepseek-v4-flash", label: "DeepSeek V4 Flash", contextWindow: 128_000 },
];

describe("reconcileConversationModels", () => {
  it("moves a conversation from a removed model to the first available model", () => {
    expect(reconcileConversationModels([conversation], models)[0]?.modelId)
      .toBe("deepseek-v4-flash");
  });

  it("keeps a valid model selection unchanged", () => {
    const current = { ...conversation, modelId: "deepseek-v4-flash" };
    expect(reconcileConversationModels([current], models)[0]).toBe(current);
  });

  it("does not rewrite conversations when the server exposes no models", () => {
    expect(reconcileConversationModels([conversation], [])).toEqual([conversation]);
  });
});
