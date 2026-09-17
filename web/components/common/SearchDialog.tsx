"use client";

/**
 * Global search (UI spec §5) and the command palette (UI §39).
 *
 * One dialog, two modes: ⌘K searches the project's data, ⌘P runs a command.
 * Both are fully keyboard operable, which is the point (UI §38, §49).
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { get } from "@/api/client";
import type { SearchResults } from "@/api/types";
import { useRouter } from "@/lib/router";
import { NAV } from "./nav";

export interface Command {
  id: string;
  label: string;
  detail: string;
  run: () => void;
}

interface Item {
  id: string;
  group: string;
  label: string;
  detail: string;
  activate: () => void;
}

export function SearchDialog({
  mode,
  onClose,
  commands,
}: {
  mode: "search" | "command";
  onClose: () => void;
  commands: Command[];
}) {
  const { navigate } = useRouter();
  const [text, setText] = useState("");
  const [results, setResults] = useState<SearchResults | null>(null);
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    input.current?.focus();
  }, []);

  const query = text.trim();
  // Too short to search is not a result worth storing: deriving it keeps the
  // effect responsible only for what the network returns.
  const visible = mode === "search" && query.length >= 2 ? results : null;

  useEffect(() => {
    if (mode !== "search" || query.length < 2) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      get<SearchResults>("/search", { q: query }, controller.signal)
        .then(setResults)
        .catch(() => setResults(null));
    }, 140);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, mode]);

  const items = useMemo<Item[]>(() => {
    if (mode === "command") {
      const needle = text.trim().toLowerCase();
      const navigation: Item[] = NAV.flatMap((group) =>
        group.items.map((entry) => ({
          id: `go:${entry.href}`,
          group: "Go to",
          label: `Open ${entry.label}`,
          detail: entry.question,
          activate: () => {
            navigate(entry.href);
            onClose();
          },
        })),
      );
      const actions: Item[] = commands.map((command) => ({
        id: command.id,
        group: "Commands",
        label: command.label,
        detail: command.detail,
        activate: () => {
          command.run();
          onClose();
        },
      }));
      const all = [...actions, ...navigation];
      return needle
        ? all.filter((item) => `${item.label} ${item.detail}`.toLowerCase().includes(needle))
        : all;
    }
    if (!visible) return [];
    return visible.hits.map((hit) => ({
      id: `${hit.group}:${hit.id}`,
      group: hit.group,
      label: hit.label,
      detail: hit.detail,
      activate: () => {
        navigate(hit.href);
        onClose();
      },
    }));
  }, [mode, visible, text, commands, navigate, onClose]);

  const [listed, setListed] = useState(`${mode}:${text}`);
  if (listed !== `${mode}:${text}`) {
    setListed(`${mode}:${text}`);
    setActive(0);
  }

  const grouped = useMemo(() => {
    const map = new Map<string, Item[]>();
    for (const item of items) {
      const bucket = map.get(item.group) ?? [];
      bucket.push(item);
      map.set(item.group, bucket);
    }
    return [...map.entries()];
  }, [items]);

  const flat = grouped.flatMap(([, bucket]) => bucket);

  return (
    <div
      className="overlay"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-label={mode === "search" ? "Search" : "Command palette"}
      >
        <input
          ref={input}
          className="dialog-input"
          value={text}
          placeholder={
            mode === "search"
              ? "Search runs, agents, tools, MCP servers, skills, datasets, errors…"
              : "Run a command…"
          }
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") onClose();
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setActive((index) => Math.min(index + 1, Math.max(flat.length - 1, 0)));
            }
            if (event.key === "ArrowUp") {
              event.preventDefault();
              setActive((index) => Math.max(index - 1, 0));
            }
            if (event.key === "Enter") {
              event.preventDefault();
              flat[active]?.activate();
            }
          }}
          aria-label={mode === "search" ? "Search" : "Command"}
        />
        <div className="dialog-list">
          {flat.length === 0 ? (
            <div style={{ padding: 14, color: "var(--text-faint)" }}>
              {mode === "search" && text.trim().length < 2
                ? "Type at least two characters."
                : "Nothing matched."}
            </div>
          ) : null}
          {grouped.map(([group, bucket]) => (
            <div key={group}>
              <div className="dialog-group">{group}</div>
              {bucket.map((item) => {
                const index = flat.indexOf(item);
                return (
                  <button
                    key={item.id}
                    type="button"
                    className="dialog-item"
                    data-active={index === active}
                    onMouseEnter={() => setActive(index)}
                    onClick={item.activate}
                  >
                    <span>{item.label}</span>
                    {item.detail ? <span className="detail">{item.detail}</span> : null}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
