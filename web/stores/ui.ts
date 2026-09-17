"use client";

/**
 * UI state only: the theme, the environment being viewed, and whether a
 * dialog is open. Server data lives in the fetch cache, never here.
 */

import { useCallback, useSyncExternalStore } from "react";

export type Theme = "dark" | "light";

const THEME_KEY = "rewyn.theme";
const ENVIRONMENT_KEY = "rewyn.environment";

function stored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function store(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* private windows and blocked storage are not errors here */
  }
}

// The theme and the selected environment live outside React -- one on the
// document element, written by a bootstrap script before first paint, the
// other in localStorage. Copying them into state inside an effect meant
// rendering once with a wrong value and again with the right one, which is
// what react-hooks/set-state-in-effect objects to. useSyncExternalStore reads
// them where they actually live, and renders the correct value the first time.

const listeners = new Set<() => void>();

function announce(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // Another tab writing the same key is a real change to the same state.
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

export function useTheme(): [Theme, () => void] {
  const theme = useSyncExternalStore(
    subscribe,
    () => (document.documentElement.dataset.theme === "light" ? "light" : "dark"),
    // The static export has no document; the shell renders dark and the
    // bootstrap script corrects it before paint if the reader prefers light.
    () => "dark" as Theme,
  );

  const toggle = useCallback(() => {
    const next: Theme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    store(THEME_KEY, next);
    announce();
  }, []);

  return [theme, toggle];
}

export function useEnvironment(): [string | null, (value: string | null) => void] {
  const environment = useSyncExternalStore(
    subscribe,
    () => stored(ENVIRONMENT_KEY),
    () => null,
  );

  const select = useCallback((value: string | null) => {
    store(ENVIRONMENT_KEY, value);
    announce();
  }, []);

  return [environment, select];
}
