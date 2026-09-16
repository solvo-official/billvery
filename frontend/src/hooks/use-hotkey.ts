import { useEffect, useRef } from "react";

export const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.userAgent);

/** True when a keyboard shortcut should be ignored because the user is typing. */
export function isTypingTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && Boolean(target.closest("input, textarea, select, [contenteditable='true']"));
}

interface HotkeyOptions {
  /** Off while false, e.g. when the owning panel is hidden. */
  enabled?: boolean;
  /** Also fire while a dialog or sheet is open (default: only when nothing modal is open). */
  allowInDialog?: boolean;
}

/**
 * A single-key shortcut ("/", "j", "Escape") that stays out of the way: it ignores keys typed
 * into fields, modified keys, and — unless allowed — keys pressed while a dialog is open.
 */
export function useHotkey(key: string, handler: (event: KeyboardEvent) => void, { enabled = true, allowInDialog = false }: HotkeyOptions = {}) {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    if (!enabled) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== key || event.metaKey || event.ctrlKey || event.altKey || isTypingTarget(event.target)) return;
      if (!allowInDialog && document.querySelector("[role='dialog'], [role='alertdialog']")) return;
      handlerRef.current(event);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [key, enabled, allowInDialog]);
}
