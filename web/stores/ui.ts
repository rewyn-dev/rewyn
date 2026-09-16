"use client";

/**
 * UI state only: the theme, the environment being viewed, and whether a
 * dialog is open. Server data lives in the fetch cache, never here.
 */

import { useCallback, useEffect, useState } from "react";

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

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>("dark");

  useEffect(() => {
    // The document already carries the resolved theme: a bootstrap script in
    // the shell applies it before first paint, so there is nothing to swap.
    const applied = document.documentElement.dataset.theme;
    setTheme(applied === "light" ? "light" : "dark");
  }, []);

  const toggle = useCallback(() => {
    setTheme((current) => {
      const next: Theme = current === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      store(THEME_KEY, next);
      return next;
    });
  }, []);

  return [theme, toggle];
}

export function useEnvironment(): [string | null, (value: string | null) => void] {
  const [environment, setEnvironment] = useState<string | null>(null);

  useEffect(() => {
    setEnvironment(stored(ENVIRONMENT_KEY));
  }, []);

  const select = useCallback((value: string | null) => {
    setEnvironment(value);
    store(ENVIRONMENT_KEY, value);
  }, []);

  return [environment, select];
}
