"use client";

/**
 * A small client-side router.
 *
 * The console is a static bundle (UI §51) served with a single-page fallback,
 * so routing happens in the browser: deep links work, the back button works,
 * and no screen needs a server round trip to render.
 */

import { createContext, useCallback, useContext, useMemo, useSyncExternalStore } from "react";
import type { ReactNode } from "react";

export interface Route {
  path: string;
  params: URLSearchParams;
}

interface RouterValue extends Route {
  navigate: (to: string, options?: { replace?: boolean }) => void;
  setParam: (key: string, value: string | null) => void;
}

const RouterContext = createContext<RouterValue | null>(null);

/**
 * The current location, as an external store.
 *
 * The bundle is prerendered at "/" and hydrated at whatever URL the reader
 * opened, so the first client render has to match the server and the real
 * location has to arrive immediately after. `useSyncExternalStore` is exactly
 * that contract: React renders the server snapshot while hydrating and then
 * re-renders with the client snapshot, patching attributes that a plain state
 * initialiser would leave stale -- the navigation telling a screen reader the
 * wrong current page, for instance (UI §49).
 */
const listeners = new Set<() => void>();

function notify(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("popstate", notify);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) window.removeEventListener("popstate", notify);
  };
}

function clientHref(): string {
  return `${window.location.pathname}${window.location.search}`;
}

function serverHref(): string {
  return "/";
}

export function RouterProvider({ children }: { children: ReactNode }) {
  const href = useSyncExternalStore(subscribe, clientHref, serverHref);

  const navigate = useCallback((to: string, options?: { replace?: boolean }) => {
    if (options?.replace) window.history.replaceState({}, "", to);
    else window.history.pushState({}, "", to);
    notify();
    window.scrollTo(0, 0);
  }, []);

  const setParam = useCallback((key: string, value: string | null) => {
    const params = new URLSearchParams(window.location.search);
    if (value === null || value === "") params.delete(key);
    else params.set(key, value);
    const search = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${search ? `?${search}` : ""}`);
    notify();
  }, []);

  const value = useMemo<RouterValue>(() => {
    const [path, search = ""] = href.split("?");
    return { path: path || "/", params: new URLSearchParams(search), navigate, setParam };
  }, [href, navigate, setParam]);

  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

export function useRouter(): RouterValue {
  const value = useContext(RouterContext);
  if (!value) throw new Error("useRouter must be used inside RouterProvider");
  return value;
}

export function Link({
  href,
  children,
  className,
  ...rest
}: { href: string; children: ReactNode; className?: string } & Record<string, unknown>) {
  const { navigate } = useRouter();
  return (
    <a
      href={href}
      className={className}
      onClick={(event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey) return;
        event.preventDefault();
        navigate(href);
      }}
      {...rest}
    >
      {children}
    </a>
  );
}

/** `/runs/abc` → `["runs", "abc"]` */
export function segments(path: string): string[] {
  return path.split("/").filter(Boolean);
}
