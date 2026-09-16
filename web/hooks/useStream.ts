"use client";

/**
 * Server-sent events (UI spec §53).
 *
 * `EventSource` reconnects on its own and gives up only when the page tells
 * it to, which is what a live view wants. A stream that ends because its run
 * finished must not be retried, so the hook closes on the server's `finished`
 * event rather than letting the browser reopen it.
 */

import { useEffect, useRef, useState } from "react";

import { BASE } from "@/api/client";

export interface StreamState<T> {
  frame: T | null;
  frames: number;
  connected: boolean;
  finished: boolean;
  error: string | null;
}

export function useStream<T>(path: string | null): StreamState<T> {
  const [state, setState] = useState<StreamState<T>>({
    frame: null,
    frames: 0,
    connected: false,
    finished: false,
    error: null,
  });
  const source = useRef<EventSource | null>(null);

  useEffect(() => {
    if (path === null) return;
    setState({ frame: null, frames: 0, connected: false, finished: false, error: null });
    const stream = new EventSource(`${BASE}${path}`);
    source.current = stream;

    stream.onopen = () => setState((current) => ({ ...current, connected: true, error: null }));
    stream.onmessage = (event: MessageEvent<string>) => {
      try {
        const frame = JSON.parse(event.data) as T;
        setState((current) => ({
          ...current,
          frame,
          frames: current.frames + 1,
          connected: true,
        }));
      } catch {
        /* a malformed frame is not worth tearing the stream down for */
      }
    };
    stream.addEventListener("finished", () => {
      stream.close();
      setState((current) => ({ ...current, finished: true, connected: false }));
    });
    stream.onerror = () => {
      setState((current) =>
        current.finished
          ? current
          : { ...current, connected: false, error: "The stream dropped. Retrying…" },
      );
    };

    return () => {
      stream.close();
      source.current = null;
    };
  }, [path]);

  return state;
}
