"use client";

/**
 * Data fetching for the console.
 *
 * Each panel fetches its own document when it is opened, and nothing else
 * (UI §50). Results are cached per URL for the life of the page so switching
 * back to a tab is instant, and every request is aborted when its screen goes
 * away so a fast click sequence cannot paint stale data.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, get, query } from "@/api/client";
import type { Params } from "@/api/client";
import type { ProblemDetail } from "@/api/types";

const cache = new Map<string, unknown>();

export function clearCache(): void {
  cache.clear();
}

export interface AsyncState<T> {
  data: T | null;
  problem: ProblemDetail | null;
  loading: boolean;
  reload: () => void;
}

export function useApi<T>(path: string | null, params?: Params): AsyncState<T> {
  const key = path === null ? null : `${path}${query(params)}`;
  const [data, setData] = useState<T | null>(() => (key ? ((cache.get(key) as T) ?? null) : null));
  const [problem, setProblem] = useState<ProblemDetail | null>(null);
  const [loading, setLoading] = useState(key !== null && !cache.has(key));
  const [nonce, setNonce] = useState(0);
  const latest = useRef(0);

  useEffect(() => {
    if (key === null || path === null) {
      setData(null);
      setLoading(false);
      return;
    }
    const cached = cache.get(key) as T | undefined;
    if (cached !== undefined && nonce === 0) {
      setData(cached);
      setProblem(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    const ticket = (latest.current += 1);
    setLoading(true);
    get<T>(path, params, controller.signal)
      .then((result) => {
        if (ticket !== latest.current) return;
        cache.set(key, result);
        setData(result);
        setProblem(null);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || ticket !== latest.current) return;
        setProblem(
          error instanceof ApiError
            ? error.problem
            : { error: "Something went wrong", detail: String(error), action: null, href: null },
        );
        setData(null);
      })
      .finally(() => {
        if (ticket === latest.current) setLoading(false);
      });
    return () => controller.abort();
    // `key` encodes both path and params, so it is the only dependency that matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce]);

  const reload = useCallback(() => {
    if (key) cache.delete(key);
    setNonce((value) => value + 1);
  }, [key]);

  return { data, problem, loading, reload };
}

/** Debounce a fast-changing value (the search box, the filter inputs). */
export function useDebounced<T>(value: T, delay = 180): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}
