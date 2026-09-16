"use client";

/**
 * The replay workspace (UI spec §20, §21).
 *
 * Two columns -- what was recorded, and what will be replayed -- over a
 * matrix of nine controls, four modes each. The server plans the replay
 * before it runs, so every control states in plain language what it will
 * actually do, including the ones this surface cannot change. Nothing here
 * pretends: a control the console cannot honour says why.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { get, post } from "@/api/client";
import { REPLAY_COMPONENTS } from "@/api/types";
import type {
  ProblemDetail,
  ReplayComponent,
  ReplayPlan,
  ReplayView,
  RunDetail,
  Setting,
} from "@/api/types";
import { useApi } from "@/hooks/useApi";
import { duration, json, money, truncate } from "@/lib/format";
import { Link } from "@/lib/router";
import { Code, Problem, Row, Skeleton, Tag } from "@/components/common/atoms";

const MODES: Setting[] = ["original", "new", "recorded", "live"];

const NEEDS_VALUE = new Set(["model", "context", "temperature", "system"]);

const PLACEHOLDER: Record<string, string> = {
  model: "anthropic:claude-opus-5",
  context: "original",
  temperature: "0.9",
  system: "New system instructions…",
};

function label(component: string): string {
  return component === "mcp" ? "MCP" : component[0]!.toUpperCase() + component.slice(1);
}

export function ReplayWorkspace({ runId }: { runId: string }) {
  const { data: run } = useApi<RunDetail>(`/runs/${runId}`);
  const [settings, setSettings] = useState<Record<string, ReplayComponent>>(() =>
    Object.fromEntries(
      REPLAY_COMPONENTS.map((component) => [
        component,
        { component, mode: "original" as Setting, value: null },
      ]),
    ),
  );
  const [plan, setPlan] = useState<ReplayPlan | null>(null);
  const [job, setJob] = useState<ReplayView | null>(null);
  const [problem, setProblem] = useState<ProblemDetail | null>(null);
  const [running, setRunning] = useState(false);

  const body = useMemo(() => ({ components: Object.values(settings) }), [settings]);

  useEffect(() => {
    const controller = new AbortController();
    post<ReplayPlan>(`/runs/${runId}/replay:plan`, body, controller.signal)
      .then(setPlan)
      .catch(() => undefined);
    return () => controller.abort();
  }, [runId, body]);

  const update = useCallback((component: string, patch: Partial<ReplayComponent>) => {
    setSettings((current) => ({
      ...current,
      [component]: { ...current[component]!, ...patch },
    }));
  }, []);

  const start = useCallback(async () => {
    setProblem(null);
    setRunning(true);
    try {
      let view = await post<ReplayView>(`/runs/${runId}/replay`, body);
      setJob(view);
      // Reconstruction finishes immediately; a live model does not.
      for (
        let attempt = 0;
        attempt < 600 && view.status !== "succeeded" && view.status !== "failed";
        attempt += 1
      ) {
        await new Promise((resolve) => setTimeout(resolve, 250));
        view = await get<ReplayView>(`/replays/${view.id}`);
        setJob(view);
      }
      if (view.problem) setProblem(view.problem);
    } catch (error) {
      const detail = (error as { problem?: ProblemDetail }).problem;
      setProblem(
        detail ?? {
          error: "Replay could not start",
          detail: String(error),
          action: null,
          href: null,
        },
      );
    } finally {
      setRunning(false);
    }
  }, [runId, body]);

  if (!run || !plan) {
    return (
      <div className="page">
        <Skeleton rows={8} />
      </div>
    );
  }

  const effects = new Map(plan.components.map((c) => [c.component, c]));
  const blocked = plan.components.filter((c) => !c.supported);

  return (
    <div className="page">
      <Row>
        <Link className="btn btn-ghost" href={`/runs/${runId}`}>
          ← Run
        </Link>
        <h1 className="page-title" style={{ margin: 0 }}>
          Replay <span className="mono">{run.run.id}</span>
        </h1>
        <Tag tone="accent">{plan.mode}</Tag>
      </Row>

      <div className="grid-2" style={{ marginTop: 12 }}>
        <div className="card">
          <div className="metric-label">Original</div>
          <Row gap={14}>
            <span>{run.run.agent ?? run.run.name}</span>
            <span className="mono">{run.run.model ?? "—"}</span>
            <span>{money(run.run.cost)}</span>
            <span>{duration(run.run.duration_ms)}</span>
          </Row>
        </div>
        <div className="card">
          <div className="metric-label">Replay</div>
          <Row gap={14}>
            <span>{plan.mode === "reconstruct" ? "the recording" : "a new model"}</span>
            <span className="mono">
              {effects.get("model")?.mode === "new" ? effects.get("model")?.value : "recorded"}
            </span>
            <span>{job ? money(job.cost) : "—"}</span>
            <span>{job ? duration(job.duration_ms) : "—"}</span>
          </Row>
        </div>
      </div>

      <h2 className="section-title">Controls</h2>
      <table className="table">
        <thead>
          <tr>
            <th scope="col" style={{ width: 120 }}>
              Component
            </th>
            <th scope="col" style={{ width: 320 }}>
              Mode
            </th>
            <th scope="col" style={{ width: 260 }}>
              Value
            </th>
            <th scope="col">What this does</th>
          </tr>
        </thead>
        <tbody>
          {REPLAY_COMPONENTS.map((component) => {
            const setting = settings[component]!;
            const effect = effects.get(component);
            return (
              <tr key={component} style={{ cursor: "default" }}>
                <td>{label(component)}</td>
                <td>
                  <Row gap={4}>
                    {MODES.map((mode) => (
                      <button
                        key={mode}
                        type="button"
                        className={setting.mode === mode ? "btn btn-primary" : "btn btn-ghost"}
                        aria-pressed={setting.mode === mode}
                        onClick={() => update(component, { mode })}
                      >
                        {mode}
                      </button>
                    ))}
                  </Row>
                </td>
                <td>
                  {setting.mode === "new" && NEEDS_VALUE.has(component) ? (
                    <>
                      <label className="visually-hidden" htmlFor={`value-${component}`}>
                        {label(component)} value
                      </label>
                      <input
                        id={`value-${component}`}
                        className="input"
                        style={{ width: "100%" }}
                        placeholder={PLACEHOLDER[component] ?? ""}
                        value={setting.value ?? ""}
                        onChange={(event) => update(component, { value: event.target.value })}
                      />
                    </>
                  ) : (
                    <span style={{ color: "var(--text-faint)" }}>—</span>
                  )}
                </td>
                <td
                  style={{
                    whiteSpace: "normal",
                    color: effect?.supported === false ? "var(--warn)" : "var(--text-muted)",
                  }}
                >
                  {effect?.effect}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {plan.notes.map((note) => (
        <p key={note} style={{ color: "var(--text-muted)" }}>
          {note}
        </p>
      ))}

      <Row>
        <button
          className="btn btn-primary"
          type="button"
          onClick={start}
          disabled={running || blocked.length > 0}
          title={
            blocked.length > 0
              ? `${blocked.map((c) => c.component).join(", ")} cannot be changed from the console`
              : undefined
          }
        >
          {running ? "Replaying…" : "Run replay"}
        </button>
        {job ? <Tag tone={job.status === "failed" ? "fail" : "accent"}>{job.status}</Tag> : null}
      </Row>

      {problem ? (
        <div style={{ marginTop: 14 }}>
          <Problem problem={problem} />
        </div>
      ) : null}

      {job && job.status === "succeeded" ? <Outcome job={job} /> : null}
    </div>
  );
}

function Outcome({ job }: { job: ReplayView }) {
  return (
    <>
      <h2 className="section-title">Output comparison</h2>
      <Row>
        <Tag tone={job.identical ? "ok" : "warn"}>
          {job.identical ? "identical output" : "output changed"}
        </Tag>
        {job.faithful === false && job.identical ? (
          <Tag tone="warn">{job.mismatches.length} call(s) did not line up with the recording</Tag>
        ) : null}
        <span style={{ color: "var(--text-muted)" }}>
          {money(job.original_cost)} → {money(job.cost)}
        </span>
      </Row>

      <div className="grid-2" style={{ marginTop: 10 }}>
        <div className="card">
          <div className="metric-label">Original</div>
          <Code label="Original output" maxHeight={260}>
            {json(job.original_output) || "(none)"}
          </Code>
        </div>
        <div className="card">
          <div className="metric-label">Replay</div>
          <Code label="Replay output" maxHeight={260}>
            {json(job.output) || "(none)"}
          </Code>
        </div>
      </div>

      {job.prompts.length > 0 ? (
        <>
          <h2 className="section-title">
            Prompts ({job.prompts.filter((p) => p.changed).length} changed)
          </h2>
          {job.prompts.map((prompt) => (
            <div className="card" key={prompt.index} style={{ marginBottom: 8 }}>
              <Row>
                <strong>Call {prompt.index + 1}</strong>
                <Tag tone={prompt.changed ? "warn" : "ok"}>
                  {prompt.changed ? "changed" : "same"}
                </Tag>
                <span className="mono" style={{ color: "var(--text-faint)" }}>
                  {prompt.original_model} → {prompt.replay_model}
                </span>
                <span className="spacer" />
                <span style={{ color: "var(--text-muted)" }}>
                  {duration(prompt.original_latency_ms)} → {duration(prompt.replay_latency_ms)}
                </span>
              </Row>
              {prompt.error ? <Tag tone="fail">{prompt.error}</Tag> : null}
              <div className="grid-2" style={{ marginTop: 8 }}>
                <Code label={`Original answer for call ${prompt.index + 1}`} maxHeight={180}>
                  {truncate(prompt.original_text, 1200) || "(none)"}
                </Code>
                <Code label={`Replay answer for call ${prompt.index + 1}`} maxHeight={180}>
                  {truncate(prompt.replay_text, 1200) || "(none)"}
                </Code>
              </div>
            </div>
          ))}
        </>
      ) : null}

      {job.mismatches.length > 0 ? (
        <>
          <h2 className="section-title">Mismatches</h2>
          {job.mismatches.map((mismatch, index) => (
            <div className="card" key={index} style={{ marginBottom: 8 }}>
              <Row>
                <Tag tone="warn">{mismatch.kind}</Tag>
                <span>{mismatch.reason.replace("_", " ")}</span>
                <span style={{ color: "var(--text-muted)" }}>{mismatch.detail}</span>
              </Row>
            </div>
          ))}
        </>
      ) : null}
    </>
  );
}
