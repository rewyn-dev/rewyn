"use client";

/**
 * Run detail: the signature screen (UI spec §8).
 *
 * The header says what happened and offers the four primary actions; the tabs
 * below are the inspector. Each tab fetches its own projection when opened,
 * so opening a run costs one small request (UI §50), and a tab that holds
 * nothing says so before you click it.
 */

import { useMemo, useState } from "react";

import type { RunDetail } from "@/api/types";
import { useApi } from "@/hooks/useApi";
import { useKeyboard } from "@/hooks/useKeyboard";
import { BASE } from "@/api/client";
import { clock, duration, json, money, tokens } from "@/lib/format";
import { Link, useRouter } from "@/lib/router";
import { SaveAsTest } from "./SaveAsTest";
import { Code, Problem, Row, Skeleton, StatusDot, Tag } from "@/components/common/atoms";
import { Comments } from "@/components/common/Comments";
import { ContextInspector } from "@/components/context/ContextInspector";
import { ExecutionGraph } from "@/components/graphs/ExecutionGraph";
import { Timeline } from "./Timeline";
import {
  DecisionsPanel,
  McpPanel,
  MemoryPanel,
  ModelPanel,
  PromptPanel,
  SkillsPanel,
  ToolsPanel,
} from "./panels";

type TabId =
  | "timeline"
  | "graph"
  | "context"
  | "model"
  | "prompt"
  | "memory"
  | "tools"
  | "mcp"
  | "skills"
  | "decisions";

const TABS: { id: TabId; label: string; counter: (detail: RunDetail) => number }[] = [
  { id: "timeline", label: "Timeline", counter: (d) => d.panels.timeline },
  { id: "graph", label: "Graph", counter: (d) => d.panels.graph },
  { id: "context", label: "Context", counter: (d) => d.panels.context },
  { id: "model", label: "Model", counter: (d) => d.panels.model },
  { id: "prompt", label: "Prompt", counter: (d) => d.panels.prompt },
  { id: "memory", label: "Memory", counter: (d) => d.panels.memory },
  { id: "tools", label: "Tools", counter: (d) => d.panels.tools },
  { id: "mcp", label: "MCP", counter: (d) => d.panels.mcp },
  { id: "skills", label: "Skills", counter: (d) => d.panels.skills },
  {
    id: "decisions",
    label: "Decisions",
    counter: (d) => d.panels.guardrails + d.panels.approvals,
  },
];

export function RunScreen({ runId }: { runId: string }) {
  const { params, setParam, navigate } = useRouter();
  const [tab, setTab] = useState<TabId>((params.get("tab") as TabId) ?? "timeline");
  const [saving, setSaving] = useState(false);
  const { data, problem, loading, reload } = useApi<RunDetail>(`/runs/${runId}`);

  const select = useMemo(
    () => (next: TabId) => {
      setTab(next);
      setParam("tab", next === "timeline" ? null : next);
    },
    [setParam],
  );

  useKeyboard(
    useMemo(
      () => [
        // The spec's shortcuts (UI §38) come first; tabs take what is left.
        { key: "r", handler: () => navigate(`/runs/${runId}/replay`), description: "Replay" },
        { key: "c", handler: () => navigate(`/compare?b=${runId}`), description: "Compare" },
        { key: "t", handler: () => setSaving(true), description: "Save as test" },
        { key: "1", handler: () => select("timeline"), description: "Timeline" },
        { key: "2", handler: () => select("graph"), description: "Graph" },
        { key: "3", handler: () => select("context"), description: "Context" },
        { key: "4", handler: () => select("tools"), description: "Tools" },
      ],
      [select, navigate, runId],
    ),
  );

  if (problem) {
    return (
      <div className="page">
        <Problem problem={problem} onRetry={reload} />
      </div>
    );
  }
  if (loading && !data) {
    return (
      <div className="page">
        <Skeleton rows={10} />
      </div>
    );
  }
  if (!data) return null;

  const run = data.run;

  return (
    <div className="page">
      <Row>
        <Link className="btn btn-ghost" href="/runs">
          ← Runs
        </Link>
        <h1 className="page-title mono" style={{ margin: 0 }}>
          {run.id}
        </h1>
      </Row>

      <Row gap={14}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <StatusDot status={run.status} />
          <strong>{run.agent ?? run.name}</strong>
          <Tag tone={run.status === "failed" ? "fail" : run.status === "succeeded" ? "ok" : "warn"}>
            {run.status}
          </Tag>
        </span>
        <span style={{ color: "var(--text-muted)" }}>{clock(run.started_at)}</span>
        <span style={{ color: "var(--text-muted)" }}>Duration {duration(run.duration_ms)}</span>
        <span style={{ color: "var(--text-muted)" }}>
          Cost {money(run.cost, data.cost.currency)}
        </span>
        <span style={{ color: "var(--text-muted)" }}>
          {tokens(run.input_tokens)} in · {tokens(run.output_tokens)} out
        </span>
        <Tag>{run.environment}</Tag>
        {run.user ? <Tag>{run.user}</Tag> : null}
      </Row>

      <Row>
        <Link className="btn" href={`/runs/${run.id}/replay`}>
          Replay <kbd>R</kbd>
        </Link>
        <Link className="btn" href={`/compare?b=${run.id}`}>
          Compare <kbd>C</kbd>
        </Link>
        <button className="btn" type="button" onClick={() => setSaving(true)}>
          Save as Test <kbd>T</kbd>
        </button>
        <a className="btn" href={`${BASE}/runs/${run.id}/export`} download>
          Export
        </a>
        {run.eval_score !== null ? (
          <Tag tone={run.eval_score >= 0.5 ? "ok" : "fail"}>score {run.eval_score.toFixed(2)}</Tag>
        ) : null}
      </Row>

      {saving ? (
        <SaveAsTest runId={run.id} output={data.output} onClose={() => setSaving(false)} />
      ) : null}

      {run.error ? (
        <div className="problem" style={{ marginTop: 14, maxWidth: "none" }}>
          <h3>{run.error}</h3>
          {data.traceback ? (
            <Code label="Traceback" maxHeight={240}>
              {data.traceback}
            </Code>
          ) : null}
        </div>
      ) : null}

      <div className="grid-2" style={{ marginTop: 14 }}>
        <div className="card">
          <div className="metric-label">Input</div>
          <Code label="Run input" maxHeight={160}>
            {json(data.input) || "(none)"}
          </Code>
        </div>
        <div className="card">
          <div className="metric-label">Output</div>
          <Code label="Run output" maxHeight={160}>
            {json(data.output) || "(none)"}
          </Code>
        </div>
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <div className="metric-label">Dependencies</div>
        <Row gap={6}>
          {data.dependencies.map((dependency) => (
            <Tag
              key={`${dependency.kind}:${dependency.name}`}
              title={dependency.fingerprint ?? undefined}
            >
              {dependency.kind}:{dependency.name}
              {dependency.version !== "unversioned" ? ` v${dependency.version}` : ""}
            </Tag>
          ))}
        </Row>
      </div>

      <div className="tabs" role="tablist" aria-label="Run detail">
        {TABS.map((entry) => {
          const total = entry.counter(data);
          return (
            <button
              key={entry.id}
              role="tab"
              type="button"
              className="tab"
              aria-selected={tab === entry.id}
              disabled={total === 0}
              onClick={() => select(entry.id)}
            >
              {entry.label}
              {total > 0 ? <span className="count">{total}</span> : null}
            </button>
          );
        })}
      </div>

      <div role="tabpanel" style={{ paddingTop: 14 }}>
        {tab === "timeline" ? <Timeline runId={runId} /> : null}
        {tab === "graph" ? <ExecutionGraph runId={runId} /> : null}
        {tab === "context" ? <ContextInspector runId={runId} /> : null}
        {tab === "model" ? <ModelPanel runId={runId} /> : null}
        {tab === "prompt" ? <PromptPanel runId={runId} /> : null}
        {tab === "memory" ? <MemoryPanel runId={runId} /> : null}
        {tab === "tools" ? <ToolsPanel runId={runId} /> : null}
        {tab === "mcp" ? <McpPanel runId={runId} /> : null}
        {tab === "skills" ? <SkillsPanel runId={runId} /> : null}
        {tab === "decisions" ? <DecisionsPanel runId={runId} /> : null}
      </div>

      <div style={{ marginTop: 14 }}>
        <Comments subject={`run:${runId}`} />
      </div>
    </div>
  );
}
