"use client";

/**
 * Agents and their version history (UI spec §29, §30).
 *
 * An agent page is not a configuration file rendered back at you: it is what
 * this agent's runs actually used, rolled up. So the configuration tab shows
 * the dependencies recorded, and the versions tab compares two of them by
 * diffing the behaviour manifests those runs produce.
 */

import { useState } from "react";

import type { AgentDetail, AgentSummary, ManifestDiff } from "@/api/types";
import { useApi } from "@/hooks/useApi";
import { ago, count, duration, money, percent } from "@/lib/format";
import { Link } from "@/lib/router";
import { Empty, Metric, Problem, Row, Skeleton, Tag } from "@/components/common/atoms";
import { RunsTable } from "@/components/runs/RunsTable";

export function AgentsScreen() {
  const { data, problem, loading, reload } = useApi<AgentSummary[]>("/agents");
  if (problem)
    return (
      <div className="page">
        <Problem problem={problem} onRetry={reload} />
      </div>
    );
  if (loading && !data)
    return (
      <div className="page">
        <Skeleton rows={6} />
      </div>
    );

  return (
    <div className="page">
      <h1 className="page-title">Agents</h1>
      <p className="page-sub">What your AI is made of, from what its runs used.</p>
      {(data ?? []).length === 0 ? (
        <Empty title="No agents yet">
          <p>
            An agent appears here once it has run. Everything on this page is derived from the
            dependencies a run records, so there is nothing to configure.
          </p>
          <Link className="btn" href="/runs">
            Browse runs
          </Link>
        </Empty>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Agent</th>
              <th scope="col">Version</th>
              <th scope="col">Model</th>
              <th scope="col">Environments</th>
              <th scope="col" className="num">
                Runs
              </th>
              <th scope="col" className="num">
                Success
              </th>
              <th scope="col" className="num">
                Avg cost
              </th>
              <th scope="col">Last run</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((agent) => (
              <tr key={agent.name}>
                <td>
                  <Link href={`/agents/${agent.name}`}>{agent.name}</Link>
                </td>
                <td className="mono">
                  v{agent.version}
                  {agent.versions > 1 ? (
                    <span style={{ color: "var(--text-faint)" }}> of {agent.versions}</span>
                  ) : null}
                </td>
                <td className="mono">{agent.model ?? "—"}</td>
                <td>{agent.environments.join(", ") || "—"}</td>
                <td className="num">{count(agent.runs)}</td>
                <td className="num">{percent(agent.success_rate)}</td>
                <td className="num">{money(agent.avg_cost)}</td>
                <td>{ago(agent.last_run_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

type Tab = "overview" | "runs" | "configuration" | "dependencies" | "evaluations" | "versions";

const TABS: Tab[] = [
  "overview",
  "runs",
  "configuration",
  "dependencies",
  "evaluations",
  "versions",
];

export function AgentScreen({ name }: { name: string }) {
  const [tab, setTab] = useState<Tab>("overview");
  const { data, problem, loading, reload } = useApi<AgentDetail>(`/agents/${name}`);

  if (problem)
    return (
      <div className="page">
        <Problem problem={problem} onRetry={reload} />
      </div>
    );
  if (loading && !data)
    return (
      <div className="page">
        <Skeleton rows={8} />
      </div>
    );
  if (!data) return null;

  return (
    <div className="page">
      <Row>
        <Link className="btn btn-ghost" href="/agents">
          ← Agents
        </Link>
        <h1 className="page-title" style={{ margin: 0 }}>
          {data.name}
        </h1>
        <Tag tone="accent">v{data.version}</Tag>
        {data.environments.map((environment) => (
          <Tag key={environment}>{environment}</Tag>
        ))}
      </Row>

      <div className="metrics" style={{ marginTop: 12 }}>
        <Metric label="Runs" value={count(data.runs)} />
        <Metric label="Success" value={percent(data.success_rate)} />
        <Metric label="Avg cost" value={money(data.avg_cost)} />
        <Metric label="Avg latency" value={duration(data.avg_latency_ms)} />
        <Metric
          label="Evaluation"
          value={data.eval_score === null ? "—" : data.eval_score.toFixed(2)}
          hint={data.eval_score === null ? "no scores recorded" : undefined}
        />
      </div>

      <div className="tabs" role="tablist" aria-label="Agent">
        {TABS.map((entry) => (
          <button
            key={entry}
            role="tab"
            type="button"
            className="tab"
            aria-selected={tab === entry}
            onClick={() => setTab(entry)}
            style={{ textTransform: "capitalize" }}
          >
            {entry}
          </button>
        ))}
      </div>

      <div role="tabpanel" style={{ paddingTop: 14 }}>
        {tab === "overview" ? <Overview agent={data} /> : null}
        {tab === "runs" ? <RunsTable runs={data.recent_runs} /> : null}
        {tab === "configuration" ? <Configuration agent={data} /> : null}
        {tab === "dependencies" ? <Dependencies agent={data} /> : null}
        {tab === "evaluations" ? <Reports agent={data} /> : null}
        {tab === "versions" ? <Versions agent={data} /> : null}
      </div>
    </div>
  );
}

function Overview({ agent }: { agent: AgentDetail }) {
  return (
    <div className="grid-2">
      <div className="card">
        <div className="metric-label">Configuration</div>
        <table className="table">
          <tbody>
            <tr style={{ cursor: "default" }}>
              <td>Model</td>
              <td className="mono">{agent.model ?? "—"}</td>
            </tr>
            <tr style={{ cursor: "default" }}>
              <td>Skills</td>
              <td>{agent.skills.join(", ") || "—"}</td>
            </tr>
            <tr style={{ cursor: "default" }}>
              <td>MCP</td>
              <td>{agent.mcp_servers.join(", ") || "—"}</td>
            </tr>
            <tr style={{ cursor: "default" }}>
              <td>Tools</td>
              <td>{count(agent.tools)}</td>
            </tr>
            <tr style={{ cursor: "default" }}>
              <td>Memory</td>
              <td>{agent.memory ? "Enabled" : "—"}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div className="card">
        <div className="metric-label">Recent runs</div>
        <RunsTable runs={agent.recent_runs.slice(0, 8)} />
      </div>
    </div>
  );
}

function Configuration({ agent }: { agent: AgentDetail }) {
  return (
    <div className="card">
      <p style={{ color: "var(--text-muted)", marginTop: 0 }}>
        Recorded configuration: what this agent&rsquo;s runs actually used, not what a file says
        they should have used.
      </p>
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Kind</th>
            <th scope="col">Name</th>
            <th scope="col">Version</th>
          </tr>
        </thead>
        <tbody>
          {agent.dependencies.map((dependency) => (
            <tr key={`${dependency.kind}:${dependency.name}`} style={{ cursor: "default" }}>
              <td>{dependency.kind}</td>
              <td className="mono">{dependency.name}</td>
              <td className="mono">{dependency.version}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Dependencies({ agent }: { agent: AgentDetail }) {
  const grouped = new Map<string, typeof agent.dependencies>();
  for (const dependency of agent.dependencies) {
    grouped.set(dependency.kind, [...(grouped.get(dependency.kind) ?? []), dependency]);
  }
  return (
    <div>
      <p style={{ color: "var(--text-muted)" }}>
        Everything this agent depends on. The full dependency graph, with drift, arrives in P3.
      </p>
      <div className="grid-2">
        {[...grouped.entries()].map(([kind, entries]) => (
          <div className="card" key={kind}>
            <div className="metric-label">{kind}</div>
            <Row gap={6}>
              {entries.map((dependency) => (
                <Tag key={dependency.name}>
                  {dependency.name}
                  {dependency.version !== "unversioned" ? ` v${dependency.version}` : ""}
                </Tag>
              ))}
            </Row>
          </div>
        ))}
      </div>
    </div>
  );
}

function Reports({ agent }: { agent: AgentDetail }) {
  if (agent.reports.length === 0) {
    return (
      <Empty title="No regression runs for this agent">
        <p>Save a run as a test, then run the dataset. Reports for this agent appear here.</p>
        <Link className="btn" href="/regression">
          Regression
        </Link>
      </Empty>
    );
  }
  return (
    <table className="table">
      <thead>
        <tr>
          <th scope="col">Report</th>
          <th scope="col">Dataset</th>
          <th scope="col" className="num">
            Tests
          </th>
          <th scope="col" className="num">
            Success
          </th>
          <th scope="col">When</th>
        </tr>
      </thead>
      <tbody>
        {agent.reports.map((report) => (
          <tr key={report.id}>
            <td className="mono">
              <Link href={`/regression/${report.id}`}>{report.id.slice(0, 14)}</Link>
            </td>
            <td>{report.dataset}</td>
            <td className="num">{count(report.tests)}</td>
            <td className="num">{percent(report.success_rate)}</td>
            <td>{ago(report.created_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Versions({ agent }: { agent: AgentDetail }) {
  const [before, setBefore] = useState<string>(agent.version_history[1]?.version ?? "");
  const [after, setAfter] = useState<string>(agent.version_history[0]?.version ?? "");
  const comparable = before && after && before !== after;
  const { data, problem } = useApi<ManifestDiff>(
    comparable ? `/agents/${agent.name}/versions/${before}..${after}` : null,
  );

  return (
    <div>
      <table className="table" style={{ maxWidth: 620 }}>
        <thead>
          <tr>
            <th scope="col">Version</th>
            <th scope="col" className="num">
              Runs
            </th>
            <th scope="col">First seen</th>
            <th scope="col">Last seen</th>
          </tr>
        </thead>
        <tbody>
          {agent.version_history.map((version) => (
            <tr key={version.version} style={{ cursor: "default" }}>
              <td className="mono">v{version.version}</td>
              <td className="num">{count(version.runs)}</td>
              <td>{ago(version.first_seen)}</td>
              <td>{ago(version.last_seen)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {agent.version_history.length < 2 ? (
        <p style={{ color: "var(--text-muted)" }}>
          One version has run so far. When a second does, you can compare them here.
        </p>
      ) : (
        <>
          <h2 className="section-title">Compare versions</h2>
          <Row>
            <label htmlFor="before">From</label>
            <select
              id="before"
              className="select"
              value={before}
              onChange={(event) => setBefore(event.target.value)}
            >
              {agent.version_history.map((version) => (
                <option key={version.version} value={version.version}>
                  v{version.version}
                </option>
              ))}
            </select>
            <label htmlFor="after">To</label>
            <select
              id="after"
              className="select"
              value={after}
              onChange={(event) => setAfter(event.target.value)}
            >
              {agent.version_history.map((version) => (
                <option key={version.version} value={version.version}>
                  v{version.version}
                </option>
              ))}
            </select>
          </Row>
          {problem ? <Problem problem={problem} /> : null}
          {data ? (
            <div style={{ marginTop: 12 }}>
              <p style={{ color: "var(--text-muted)" }}>
                {data.findings.length} change{data.findings.length === 1 ? "" : "s"},{" "}
                {data.unchanged} dependencies unchanged.
              </p>
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Kind</th>
                    <th scope="col">Name</th>
                    <th scope="col">Change</th>
                    <th scope="col">Likely cause</th>
                  </tr>
                </thead>
                <tbody>
                  {data.findings.map((finding) => (
                    <tr
                      key={`${finding.dependency_kind}:${finding.name}`}
                      style={{ cursor: "default" }}
                    >
                      <td>{finding.dependency_kind}</td>
                      <td className="mono">{finding.name}</td>
                      <td>
                        <Tag tone={finding.kind === "removed" ? "fail" : "warn"}>
                          {finding.kind.replace("_", " ")}
                        </Tag>{" "}
                        {finding.before_version && finding.after_version ? (
                          <span className="mono">
                            v{finding.before_version} → v{finding.after_version}
                          </span>
                        ) : null}
                      </td>
                      <td style={{ whiteSpace: "normal", color: "var(--text-muted)" }}>
                        {finding.likely_cause}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
