"use client";

/**
 * The overview (UI spec §6).
 *
 * It answers one question -- "is my AI system healthy?" -- and then shows the
 * three things that follow from a bad answer: what changed, what looks wrong,
 * and what ran. No chart appears unless it answers a question (UI §46), and
 * none does here: five numbers and three lists say more.
 */

import type { Overview as OverviewDocument } from "@/api/types";
import { useApi } from "@/hooks/useApi";
import { ago, day, duration, money, percent } from "@/lib/format";
import { Link } from "@/lib/router";
import { Empty, Metric, Problem, Skeleton, StatusDot, Tag } from "./atoms";
import { RunsTable } from "@/components/runs/RunsTable";

/** Where a number is explained. Cost and regression have no §4 nav slot. */
const LINKS: Record<string, string> = {
  "Avg cost": "/cost",
  Regression: "/regression",
  Runs: "/runs",
};

const STATUS_LABEL: Record<string, string> = {
  healthy: "Healthy",
  degraded: "Degraded",
  unhealthy: "Unhealthy",
  unknown: "No data",
};

function value(metric: OverviewDocument["metrics"][number]): string {
  switch (metric.unit) {
    case "percent":
      return percent(metric.value);
    case "seconds":
      return duration(metric.value * 1000);
    case "currency":
      return money(metric.value);
    default:
      return metric.value.toLocaleString();
  }
}

export function OverviewScreen({ environment }: { environment: string | null }) {
  const { data, problem, loading, reload } = useApi<OverviewDocument>("/overview", {
    environment,
  });

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
        <Skeleton rows={8} />
      </div>
    );
  }
  if (!data) return null;

  if (data.recent_runs.length === 0) {
    return (
      <div className="page">
        <Empty title="No runs yet">
          <p>Install the SDK and record your first run:</p>
          <pre>{`pip install rewyn

from rewyn.core.run import start_run

with start_run("my-agent") as run:
    ...`}</pre>
          <p>Everything on this screen is derived from runs, so it fills in as they arrive.</p>
        </Empty>
      </div>
    );
  }

  return (
    <div className="page">
      <h1 className="page-title">{data.project}</h1>
      <p className="page-sub">
        <StatusDot status={data.status === "healthy" ? "succeeded" : "failed"} />{" "}
        {STATUS_LABEL[data.status] ?? data.status}
        {data.environment ? ` · ${data.environment}` : ""}
      </p>

      <div className="metrics">
        {data.metrics.map((metric) => (
          <Metric
            key={metric.label}
            label={metric.label}
            value={value(metric)}
            href={LINKS[metric.label]}
          />
        ))}
      </div>

      <div className="grid-2" style={{ marginTop: 18 }}>
        <section className="card" aria-labelledby="recent-changes">
          <h2 className="section-title" id="recent-changes" style={{ marginTop: 0 }}>
            Recent changes
          </h2>
          {data.recent_changes.length === 0 ? (
            <p style={{ color: "var(--text-faint)" }}>
              Nothing underneath this project changed version recently.
            </p>
          ) : (
            data.recent_changes.map((change) => (
              <div className="list-row" key={`${change.run_id}:${change.kind}:${change.name}`}>
                <span className="list-when">{ago(change.at)}</span>
                <span>
                  <Tag tone="accent">{change.kind}</Tag> {change.summary}
                </span>
                <span className="spacer" />
                <Link className="mono" href={`/runs/${change.run_id}`}>
                  {day(change.at)}
                </Link>
              </div>
            ))
          )}
        </section>

        <section className="card" aria-labelledby="incidents">
          <h2 className="section-title" id="incidents" style={{ marginTop: 0 }}>
            Recent incidents
            <Link className="btn btn-ghost" href="/incidents" style={{ marginLeft: 8 }}>
              All
            </Link>
          </h2>
          {data.incidents.length === 0 ? (
            <p style={{ color: "var(--text-faint)" }}>No failure clusters or cost spikes.</p>
          ) : (
            data.incidents.map((incident) => (
              <div className="list-row" key={incident.id}>
                <Tag tone={incident.severity === "critical" ? "fail" : "warn"}>
                  {incident.severity}
                </Tag>
                <span>
                  <Link href={`/incidents/${incident.id}`}>{incident.summary}</Link>
                  {incident.likely_cause ? (
                    <span style={{ color: "var(--text-faint)" }}> — {incident.likely_cause}</span>
                  ) : null}
                </span>
                <span className="spacer" />
                {incident.run_ids[0] ? (
                  <Link className="btn btn-ghost" href={`/runs/${incident.run_ids[0]}`}>
                    Inspect
                  </Link>
                ) : null}
              </div>
            ))
          )}
        </section>
      </div>

      <h2 className="section-title">Recent runs</h2>
      <RunsTable runs={data.recent_runs} />
      <div style={{ marginTop: 12 }}>
        <Link className="btn" href="/runs">
          All runs
        </Link>
      </div>
    </div>
  );
}
