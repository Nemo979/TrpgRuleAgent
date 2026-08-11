export type ComposerKeyEvent = {
  key: string;
  shiftKey: boolean;
  isComposing: boolean;
  keyCode?: number;
};

export function shouldSubmitComposerOnKeyDown(event: ComposerKeyEvent): boolean {
  return (
    event.key === "Enter"
    && !event.shiftKey
    && !event.isComposing
    // WebKit can report compositionend before the final keydown; 229 is the
    // legacy process-key signal used by IMEs during that transition.
    && event.keyCode !== 229
  );
}
