"use client";

/**
 * The application shell (UI spec §4, §38, §41, §42).
 *
 * Sidebar, project and environment selectors, global search, command palette
 * and the theme toggle. Everything that is global lives here so a screen only
 * ever renders its own content.
 */

import { useMemo, useState } from "react";
import type { ReactNode } from "react";

import type { Capabilities, EnvironmentView, NotificationView } from "@/api/types";
import { useApi } from "@/hooks/useApi";
import { useKeyboard } from "@/hooks/useKeyboard";
import type { Shortcut } from "@/hooks/useKeyboard";
import { Link, useRouter } from "@/lib/router";
import { useEnvironment, useTheme } from "@/stores/ui";
import { LOOP_COMMANDS } from "./nav";
import { Sidebar } from "./Sidebar";
import { SearchDialog } from "./SearchDialog";
import type { Command } from "./SearchDialog";

export interface ShellContext {
  capabilities: Capabilities | null;
  environment: string | null;
}

export function AppShell({
  children,
  extraShortcuts = [],
  extraCommands = [],
  onContext,
}: {
  children: (context: ShellContext) => ReactNode;
  extraShortcuts?: Shortcut[];
  extraCommands?: Command[];
  onContext?: (context: ShellContext) => void;
}) {
  const { navigate } = useRouter();
  const [dialog, setDialog] = useState<"search" | "command" | null>(null);
  const [theme, toggleTheme] = useTheme();
  const [environment, setEnvironment] = useEnvironment();
  const { data: capabilities } = useApi<Capabilities>("/capabilities");
  const { data: environments } = useApi<EnvironmentView[]>("/environments");
  // UI §40: meaningful events, surfaced without adding a navigation entry the
  // spec's sidebar does not have.
  const { data: notifications } = useApi<NotificationView[]>("/notifications");
  const urgent = (notifications ?? []).filter((item) => item.severity !== "info").length;

  const commands = useMemo<Command[]>(
    () => [
      ...extraCommands,
      {
        id: "theme",
        label: `Switch to ${theme === "dark" ? "light" : "dark"} theme`,
        detail: "",
        run: toggleTheme,
      },
      {
        id: "runs",
        label: "Open runs",
        detail: "What is my AI doing?",
        run: () => navigate("/runs"),
      },
      // UI §60: every step of the loop reachable without leaving the screen.
      ...LOOP_COMMANDS.map((entry) => ({
        id: entry.href,
        label: entry.label,
        detail: entry.question,
        run: () => navigate(entry.href),
      })),
    ],
    [extraCommands, theme, toggleTheme, navigate],
  );

  const shortcuts = useMemo<Shortcut[]>(
    () => [
      { key: "k", meta: true, handler: () => setDialog("search"), description: "Search" },
      { key: "p", meta: true, handler: () => setDialog("command"), description: "Command palette" },
      { key: "Escape", handler: () => setDialog(null), description: "Close" },
      ...extraShortcuts,
    ],
    [extraShortcuts],
  );

  useKeyboard(shortcuts);

  const context: ShellContext = { capabilities, environment };
  onContext?.(context);

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <Sidebar capabilities={capabilities} />
      <div className="main">
        <header className="topbar">
          <button className="search-trigger" type="button" onClick={() => setDialog("search")}>
            <span aria-hidden="true">⌕</span>
            <span>Search</span>
            <span className="spacer" />
            <kbd>⌘K</kbd>
          </button>
          <button className="btn btn-ghost" type="button" onClick={() => setDialog("command")}>
            Commands <kbd>⌘P</kbd>
          </button>
          <span className="spacer" />
          <label className="visually-hidden" htmlFor="environment">
            Environment
          </label>
          <select
            id="environment"
            className="select"
            value={environment ?? ""}
            onChange={(event) => setEnvironment(event.target.value || null)}
          >
            <option value="">All environments</option>
            {(environments ?? []).map((item) => (
              <option key={item.name} value={item.name}>
                {item.name}
              </option>
            ))}
          </select>
          <Link
            className="btn btn-ghost"
            href="/notifications"
            aria-label={`Notifications: ${urgent} needing attention`}
            title="Notifications"
          >
            <span aria-hidden="true">◔</span>
            {urgent > 0 ? <span className="tag tag-warn">{urgent}</span> : null}
          </Link>
          <button
            className="btn btn-ghost"
            type="button"
            onClick={toggleTheme}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          >
            {theme === "dark" ? "◐" : "◑"}
          </button>
        </header>
        <main className="content" id="main">
          {children(context)}
        </main>
      </div>
      {dialog ? (
        <SearchDialog mode={dialog} commands={commands} onClose={() => setDialog(null)} />
      ) : null}
    </div>
  );
}
