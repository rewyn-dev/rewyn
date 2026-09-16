"use client";

/**
 * Incidents (UI spec §36).
 *
 * A failed run becomes an incident, and the incident gets the timeline §36
 * draws: deployment → behavior change → detection → investigation → fix →
 * regression → resolved.
 *
 * The design decision that matters here is that the timeline always shows
 * all seven steps. A product that only drew the steps it could fill would
 * hide the interesting part -- that nobody has taken this, or that no
 * regression test covers it yet. So an unreached step is drawn dimmed, with
 * the sentence that says what would reach it, and it is a to-do list as much
 * as a history.
 */

import { useState } from "react";

import { post } from "@/api/client";
import type { IncidentDetail, IncidentStage, IncidentStatus } from "@/api/types";
import { Comments } from "@/components/common/Comments";
import { Empty, Problem, Row, Skeleton, Tag } from "@/components/common/atoms";
import { RunsTable } from "@/components/runs/RunsTable";
import { useApi } from "@/hooks/useApi";
import { ago, when } from "@/lib/format";
import { Link } from "@/lib/router";

const STATUSES: IncidentStatus[] = ["open", "investigating", "mitigated", "resolved"];

const STAGE_LABELS: Record<string, string> = {
  deployment: "Deployment",
  behavior_change: "Behavior change",
  detection: "Detection",
  investigation: "Investigation",
  fix: "Fix",
  regression: "Regression",
  resolved: "Resolved",
};

function statusTone(status: IncidentStatus): "ok" | "warn" | "fail" | "default" {
  if (status === "resolved") return "ok";
  if (status === "mitigated") return "warn";
  if (status === "investigating") return "default";
  return "fail";
}

export function IncidentsScreen() {
  const { data, problem, loading } = useApi<IncidentDetail[]>("/incidents");

  if (problem)
    return (
      <div className="page">
        <Problem problem={problem} />
      </div>
    );
  if (loading && !data)
    return (
      <div className="page">
        <Skeleton rows={5} />
      </div>
    );

  const incidents = data ?? [];

  return (
    <div className="page">
      <h1 className="page-title">Incidents</h1>
      <p className="page-sub">Failures that happened more than once, and what moved before them.</p>

      {incidents.length === 0 ? (
        <Empty title="Nothing is on fire">
          <p>
            An incident appears when several runs fail the same way inside a day, or when average
            cost jumps. Both are computed from your runs, so there is nothing to declare.
          </p>
          <Link className="btn" href="/runs?status=failed">
            Failed runs
          </Link>
        </Empty>
      ) : (
        incidents.map((incident) => (
          <div className="card" key={incident.id} style={{ marginBottom: 12 }}>
            <Row>
              <Tag tone={incident.severity === "critical" ? "fail" : "warn"}>
                {incident.severity}
              </Tag>
              <Link href={`/incidents/${incident.id}`}>
                <strong>{incident.summary}</strong>
              </Link>
              <span className="spacer" />
              <Tag tone={statusTone(incident.status)}>{incident.status}</Tag>
            </Row>
            <div className="ctx-meta" style={{ marginTop: 8 }}>
              <span>
                Affected runs <b>{incident.affected_runs}</b>
              </span>
              {incident.started_at ? (
                <span>
                  Started <b>{ago(incident.started_at)}</b>
                </span>
              ) : null}
              {incident.agent ? (
                <span>
                  Agent <b>{incident.agent}</b>
                </span>
              ) : null}
              {incident.assignee ? (
                <span>
                  Owner <b>{incident.assignee}</b>
                </span>
              ) : null}
              <span>
                Timeline{" "}
                <b>
                  {incident.timeline.filter((stage) => stage.reached).length}/
                  {incident.timeline.length}
                </b>
              </span>
            </div>
            {incident.likely_cause ? (
              <p style={{ margin: "8px 0 0", color: "var(--text-muted)" }}>
                Likely cause: {incident.likely_cause}
              </p>
            ) : null}
          </div>
        ))
      )}
    </div>
  );
}

function Stage({ stage }: { stage: IncidentStage }) {
  return (
    <li className="stage" data-reached={stage.reached}>
      <span className="stage-mark" aria-hidden="true" />
      <div className="stage-body">
        <Row gap={8}>
          <strong>{STAGE_LABELS[stage.stage] ?? stage.stage}</strong>
          {stage.at ? (
            <span style={{ color: "var(--text-faint)" }}>{when(stage.at)}</span>
          ) : (
            <span className="tag">not yet</span>
          )}
        </Row>
        <p
          style={{ margin: "2px 0 0", color: stage.reached ? "var(--text)" : "var(--text-faint)" }}
        >
          {stage.summary}
        </p>
        {stage.evidence ? (
          <p style={{ margin: "2px 0 0", color: "var(--text-faint)" }}>{stage.evidence}</p>
        ) : null}
        {stage.href ? (
          <Link className="btn btn-ghost" href={stage.href} style={{ marginTop: 4 }}>
            Open the evidence
          </Link>
        ) : null}
      </div>
    </li>
  );
}

export function IncidentScreen({ id }: { id: string }) {
  const { data, problem, reload } = useApi<IncidentDetail>(`/incidents/${id}`);
  const [busy, setBusy] = useState(false);
  const [owner, setOwner] = useState("");

  if (problem)
    return (
      <div className="page">
        <Problem problem={problem} />
      </div>
    );
  if (!data)
    return (
      <div className="page">
        <Skeleton rows={8} />
      </div>
    );

  const update = async (body: Record<string, unknown>) => {
    setBusy(true);
    try {
      await post(`/incidents/${id}`, body);
      reload();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <Row>
        <Link className="btn btn-ghost" href="/incidents">
          ← Incidents
        </Link>
        <Tag tone={data.severity === "critical" ? "fail" : "warn"}>{data.severity}</Tag>
        <span className="spacer" />
        <label className="visually-hidden" htmlFor="incident-status">
          Status
        </label>
        <select
          id="incident-status"
          className="select"
          value={data.status}
          disabled={busy}
          onChange={(event) => void update({ status: event.target.value })}
        >
          {STATUSES.map((status) => (
            <option key={status} value={status}>
              {status}
            </option>
          ))}
        </select>
      </Row>

      <h1 className="page-title" style={{ marginTop: 10 }}>
        {data.summary}
      </h1>

      <div className="metrics">
        <div>
          <div className="metric-label">Affected runs</div>
          <div className="metric-value">{data.affected_runs}</div>
        </div>
        <div>
          <div className="metric-label">Started</div>
          <div className="metric-value">{data.started_at ? ago(data.started_at) : "—"}</div>
        </div>
        <div>
          <div className="metric-label">Last seen</div>
          <div className="metric-value">{data.last_seen ? ago(data.last_seen) : "—"}</div>
        </div>
        <div>
          <div className="metric-label">Owner</div>
          <div className="metric-value">{data.assignee ?? "unassigned"}</div>
        </div>
      </div>

      <div className="grid-2" style={{ marginTop: 14 }}>
        <section className="card" aria-labelledby="cause">
          <h2 className="section-title" id="cause" style={{ marginTop: 0 }}>
            Likely cause
          </h2>
          {data.cause_evidence.length === 0 ? (
            <p style={{ color: "var(--text-faint)" }}>Nothing recorded points at a cause.</p>
          ) : (
            data.cause_evidence.map((line) => {
              const [label, ...rest] = line.split(":");
              return (
                <p key={line} style={{ margin: "0 0 6px" }}>
                  <Tag tone={label === "Observed" ? "accent" : "warn"}>{label}</Tag>{" "}
                  {rest.join(":").trim()}
                </p>
              );
            })
          )}
          {data.agent ? (
            <Row gap={6}>
              <Link className="btn btn-ghost" href={`/workspace?agent=${data.agent}`}>
                Open the workspace
              </Link>
              <Link className="btn btn-ghost" href={`/drift?agent=${data.agent}`}>
                Check drift
              </Link>
            </Row>
          ) : null}
        </section>

        <section className="card" aria-labelledby="owner">
          <h2 className="section-title" id="owner" style={{ marginTop: 0 }}>
            Ownership
          </h2>
          <p style={{ color: "var(--text-muted)", marginTop: 0 }}>
            {data.assignee
              ? `${data.assignee} is on this.`
              : "Nobody has taken this. Assigning it reaches the investigation step."}
          </p>
          <form
            style={{ display: "flex", gap: 8 }}
            onSubmit={(event) => {
              event.preventDefault();
              if (owner.trim()) void update({ assignee: owner.trim(), status: "investigating" });
            }}
          >
            <label className="visually-hidden" htmlFor="assignee">
              Assign to
            </label>
            <input
              id="assignee"
              className="input"
              style={{ flex: 1 }}
              placeholder={data.assignee ?? "Assign to…"}
              value={owner}
              onChange={(event) => setOwner(event.target.value)}
            />
            <button className="btn" type="submit" disabled={busy || !owner.trim()}>
              Assign
            </button>
          </form>
          {data.note ? (
            <p style={{ marginBottom: 0, color: "var(--text-muted)" }}>{data.note}</p>
          ) : null}
        </section>
      </div>

      <h2 className="section-title">Timeline</h2>
      <ol className="stages">
        {data.timeline.map((stage) => (
          <Stage key={stage.stage} stage={stage} />
        ))}
      </ol>

      <h2 className="section-title">Runs in this incident</h2>
      <RunsTable runs={data.runs} />

      <div style={{ marginTop: 14 }}>
        <Comments subject={`incident:${data.id}`} onChange={reload} />
      </div>
    </div>
  );
}
