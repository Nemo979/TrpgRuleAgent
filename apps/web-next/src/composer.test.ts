import { describe, expect, it } from "vitest";
import { shouldSubmitComposerOnKeyDown } from "./composer";

describe("composer keyboard handling", () => {
  it("submits a plain Enter key", () => {
    expect(shouldSubmitComposerOnKeyDown({
      key: "Enter",
      shiftKey: false,
      isComposing: false,
    })).toBe(true);
  });

  it("does not submit Enter while an IME is composing", () => {
    expect(shouldSubmitComposerOnKeyDown({
      key: "Enter",
      shiftKey: false,
      isComposing: true,
    })).toBe(false);
  });

  it("does not submit WebKit's IME process key", () => {
    expect(shouldSubmitComposerOnKeyDown({
      key: "Enter",
      shiftKey: false,
      isComposing: false,
      keyCode: 229,
    })).toBe(false);
  });

  it("keeps Shift+Enter as a newline", () => {
    expect(shouldSubmitComposerOnKeyDown({
      key: "Enter",
      shiftKey: true,
      isComposing: false,
    })).toBe(false);
  });
});
