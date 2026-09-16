"use client";

/**
 * Keyboard shortcuts (UI spec §38).
 *
 * Shortcuts never fire while the user is typing, and every one of them has a
 * visible affordance somewhere on the screen: the point is speed for people
 * who know the product, not a hidden interface for people who do not.
 */

import { useEffect } from "react";

export interface Shortcut {
  key: string;
  meta?: boolean;
  handler: () => void;
  description: string;
}

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT"
  );
}

export function useKeyboard(shortcuts: Shortcut[], enabled = true): void {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (event: KeyboardEvent) => {
      const meta = event.metaKey || event.ctrlKey;
      for (const shortcut of shortcuts) {
        const wantsMeta = shortcut.meta === true;
        if (wantsMeta !== meta) continue;
        if (event.key.toLowerCase() !== shortcut.key.toLowerCase()) continue;
        if (!wantsMeta && typing(event.target)) continue;
        event.preventDefault();
        shortcut.handler();
        return;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shortcuts, enabled]);
}
