"use client";

/**
 * Dependencies, drift, cost and releases (UI spec §28, §31-§33, §40, §43).
 *
 * These screens make claims about causes and about whether something is safe
 * to deploy, so each one shows the evidence next to the claim: the dependency
 * whose version moved, the gate that failed, the runs a cost came from. Where
 * there is no evidence the screen says so instead of filling the space.
 */

import { useState } from "react";

import { ApiError, post } from "@/api/client";
import type {
  CostView,
  DependencyMap,
  DependencyNode,
  DriftView,
  ExperimentView,
  NotificationView,
  ProblemDetail,
  ReleaseView,
} from "@/api/types";
import { useApi } from "@/hooks/useApi";
import { ago, count, duration, money, percent } from "@/lib/format";
import { Link, useRouter } from "@/lib/router";
import { Empty, Metric, Problem, Row, Skeleton, Tag } from "@/components/common/atoms";

function AgentPicker({
  value,
  onChange,
  label,
}: {
  value: string;
  onChange: (next: string) => void;
  label: string;
}) {
  const { data } = useApi<{ name: string }[]>("/agents");
  return (
    <Row>
      <label htmlFor="agent-picker">{label}</label>
      <select
        id="agent-picker"
        className="select"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">Choose an agent…</option>
        {(data ?? []).map((agent) => (
          <option key={agent.name} value={agent.name}>
            {agent.name}
          </option>
        ))}
      </select>
    </Row>
  );
}

const NODE_WIDTH = 190;
const NODE_HEIGHT = 44;
const GAP_X = 90;
const GAP_Y = 16;

/** A two-level map: the root on the left, what it uses to its right. */
function DependencyPicture({ map }: { map: DependencyMap }) {
  const { navigate } = useRouter();
  const children = new Map<string, DependencyNode[]>();
  const byId = new Map(map.nodes.map((node) => [node.id, node]));
  for (const edge of map.edges) {
    const node = byId.get(edge.target);
    if (node) children.set(edge.source, [...(children.get(edge.source) ?? []), node]);
  }

  const placed: { node: DependencyNode; x: number; y: number }[] = [];
  let row = 0;
  const walk = (id: string, depth: number) => {
    const node = byId.get(id);
    if (!node) return;
    placed.push({ node, x: depth * (NODE_WIDTH + GAP_X), y: row * (NODE_HEIGHT + GAP_Y) });
    row += 1;
    for (const child of children.get(id) ?? []) walk(child.id, depth + 1);
  };
  walk(map.root, 0);

  const width = (Math.max(...placed.map((p) => p.x), 0) + NODE_WIDTH + 40) | 0;
  const height = Math.max(row, 1) * (NODE_HEIGHT + GAP_Y) + 10;
  const position = new Map(placed.map((p) => [p.node.id, p]));

  return (
    <div className="graph-wrap">
      <svg
        width={width}
        height={height}
        role="group"
        aria-label={`Dependency map for ${map.agent}`}
      >
        {map.edges.map((edge) => {
          const from = position.get(edge.source);
          const to = position.get(edge.target);
          if (!from || !to) return null;
          const startX = from.x + NODE_WIDTH;
          const startY = from.y + NODE_HEIGHT / 2;
          const endX = to.x;
          const endY = to.y + NODE_HEIGHT / 2;
          const mid = (startX + endX) / 2;
          return (
            <path
              key={`${edge.source}->${edge.target}`}
              d={`M ${startX} ${startY} C ${mid} ${startY}, ${mid} ${endY}, ${endX} ${endY}`}
              fill="none"
              stroke={edge.relation === "exposes" ? "var(--accent)" : "var(--border-strong)"}
              strokeWidth={1.4}
            />
          );
        })}
        {placed.map(({ node, x, y }) => (
          <g
            key={node.id}
            className="graph-node"
            transform={`translate(${x + 8} ${y + 4})`}
            role={node.href ? "button" : undefined}
            tabIndex={node.href ? 0 : undefined}
            aria-label={`${node.kind} ${node.name}`}
            onClick={() => node.href && navigate(node.href)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && node.href) navigate(node.href);
            }}
          >
            <rect
              width={NODE_WIDTH}
              height={NODE_HEIGHT}
              rx={6}
              fill="var(--bg-raised)"
              stroke={node.id === map.root ? "var(--accent)" : "var(--border-strong)"}
            />
            <text x={12} y={19}>
              {node.name.length > 24 ? `${node.name.slice(0, 23)}…` : node.name}
            </text>
            <text className="sub" x={12} y={34}>
              {node.kind}
              {node.version !== "unversioned" ? ` · v${node.version}` : ""}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

export function DependenciesScreen() {
  const { params, setParam } = useRouter();
  const agent = params.get("agent") ?? "";
  const { data, problem, loading } = useApi<DependencyMap>(
    agent ? "/dependencies" : null,
    agent ? { agent } : undefined,
  );

  return (
    <div className="page">
      <h1 className="page-title">Dependencies</h1>
      <p className="page-sub">What could have changed outside my code?</p>
      <AgentPicker value={agent} label="Agent" onChange={(next) => setParam("agent", next)} />

      {!agent ? (
        <Empty title="Pick an agent">
          <p>
            A dependency map is drawn from what an agent&rsquo;s runs recorded, so it starts from an
            agent.
          </p>
        </Empty>
      ) : null}
      {problem ? <Problem problem={problem} /> : null}
      {loading && !data ? <Skeleton rows={6} /> : null}
      {data ? (
        <>
          <p style={{ color: "var(--text-muted)" }}>
            {count(data.nodes.length - 1)} dependencies over {count(data.runs)} runs. Every node is
            clickable. A tool sits under the MCP server or skill that exposed it; everything else
            hangs off the agent, because nothing claimed it.
          </p>
          <DependencyPicture map={data} />
        </>
      ) : null}
    </div>
  );
}

export function DriftScreen() {
  const { params, setParam } = useRouter();
  const agent = params.get("agent") ?? "";
  const { data, problem, loading } = useApi<DriftView>(
    agent ? "/drift" : null,
    agent ? { agent } : undefined,
  );

  return (
    <div className="page page-narrow">
      <h1 className="page-title">Drift</h1>
      <p className="page-sub">Is behaviour moving, and what moved underneath it?</p>
      <AgentPicker value={agent} label="Agent" onChange={(next) => setParam("agent", next)} />

      {!agent ? (
        <Empty title="Pick an agent">
          <p>Drift compares two windows of an agent&rsquo;s runs, so it starts from an agent.</p>
        </Empty>
      ) : null}
      {problem ? <Problem problem={problem} /> : null}
      {loading && !data ? <Skeleton rows={5} /> : null}
      {data ? (
        <>
          <div className="metrics" style={{ marginTop: 12 }}>
            <Metric
              label="Expected success"
              value={percent(data.expected_success)}
              hint={`${count(data.baseline_runs)} earlier runs`}
            />
            <Metric
              label="Current"
              value={percent(data.current_success)}
              hint={`${count(data.current_runs)} recent runs`}
            />
            <Metric label="Expected cost" value={money(data.expected_cost)} />
            <Metric label="Current cost" value={money(data.current_cost)} />
          </div>

          <h2 className="section-title">Possible causes</h2>
          {data.causes.length === 0 ? (
            <p style={{ color: "var(--text-muted)" }}>
              Nothing changed underneath this agent, and its behaviour did not move.
            </p>
          ) : (
            data.causes.map((cause, index) => (
              <div
                className="card"
                key={`${cause.kind}:${cause.name}:${index}`}
                style={{ marginBottom: 8 }}
              >
                <Row>
                  <Tag tone={cause.kind === "unexplained" ? "warn" : "accent"}>
                    {cause.kind.replace("_", " ")}
                  </Tag>
                  {cause.name ? (
                    <strong>
                      {cause.dependency_kind}: {cause.name}
                    </strong>
                  ) : null}
                  {cause.before && cause.after ? (
                    <span className="mono" style={{ color: "var(--text-faint)" }}>
                      {cause.before.slice(0, 18)} → {cause.after.slice(0, 18)}
                    </span>
                  ) : null}
                </Row>
                <p style={{ margin: "6px 0 0", color: "var(--text-muted)" }}>{cause.detail}</p>
              </div>
            ))
          )}

          {data.unchanged.length > 0 ? (
            <>
              <h2 className="section-title">Unchanged</h2>
              <Row gap={6}>
                {data.unchanged.map((name) => (
                  <Tag key={name}>{name}</Tag>
                ))}
              </Row>
              <p style={{ color: "var(--text-faint)" }}>
                What did not change is evidence too: it rules these out.
              </p>
            </>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

const GROUPS = ["agent", "model", "user", "environment", "tool", "skill", "mcp", "time"];

export function CostScreen() {
  const [groupBy, setGroupBy] = useState("agent");
  const { data, problem, loading } = useApi<CostView>("/cost", { group_by: groupBy });

  return (
    <div className="page page-narrow">
      <h1 className="page-title">Cost</h1>
      <p className="page-sub">What the spend went on, and what a successful task cost.</p>

      {problem ? <Problem problem={problem} /> : null}
      {loading && !data ? <Skeleton rows={6} /> : null}
      {data ? (
        <>
          <div className="metrics">
            <Metric label="Total" value={money(data.total, data.currency)} />
            <Metric
              label="Per successful task"
              value={
                data.per_successful_task === null
                  ? "—"
                  : money(data.per_successful_task, data.currency)
              }
              hint={`${count(data.succeeded)} of ${count(data.runs)} runs succeeded`}
            />
            <Metric label="Model" value={money(data.categories.model, data.currency)} />
            <Metric label="Tools" value={money(data.categories.tool, data.currency)} />
            <Metric label="Embedding" value={money(data.categories.embedding, data.currency)} />
            <Metric label="Retrieval" value={money(data.categories.retrieval, data.currency)} />
            <Metric label="Sandbox" value={money(data.categories.sandbox, data.currency)} />
          </div>

          <Row>
            <label htmlFor="group-by">Group by</label>
            <select
              id="group-by"
              className="select"
              value={groupBy}
              onChange={(event) => setGroupBy(event.target.value)}
            >
              {GROUPS.map((group) => (
                <option key={group} value={group}>
                  {group}
                </option>
              ))}
            </select>
          </Row>

          <table className="table" style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th scope="col">{groupBy}</th>
                <th scope="col" className="num">
                  Cost
                </th>
                <th scope="col" className="num">
                  Share
                </th>
                <th scope="col" className="num">
                  Runs
                </th>
                <th scope="col" className="num">
                  Per run
                </th>
                <th scope="col" className="num">
                  Per successful task
                </th>
              </tr>
            </thead>
            <tbody>
              {data.slices.map((slice) => (
                <tr key={slice.key} style={{ cursor: "default" }}>
                  <td>{slice.key}</td>
                  <td className="num">{money(slice.total, data.currency)}</td>
                  <td className="num">{percent(slice.share)}</td>
                  <td className="num">{count(slice.runs)}</td>
                  <td className="num">{money(slice.per_run, data.currency)}</td>
                  <td className="num">
                    {slice.per_successful_task === null
                      ? "—"
                      : money(slice.per_successful_task, data.currency)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
    </div>
  );
}

/**
 * The §43 promote action.
 *
 * The button is disabled when the gate has not passed, but that is a courtesy
 * — the server refuses the same request regardless, and when it does, the
 * refusal is shown with the action that fixes it rather than a status code
 * (UI §48, §54).
 */
function PromoteButton({ release, onPromoted }: { release: ReleaseView; onPromoted: () => void }) {
  const [problem, setProblem] = useState<ProblemDetail | null>(null);
  const [busy, setBusy] = useState(false);

  if (release.promoted) {
    return (
      <Row gap={6}>
        <Tag tone="ok">promoted to {release.promoted.environment}</Tag>
        <span style={{ color: "var(--text-faint)" }}>
          by {release.promoted.by} · {ago(release.promoted.at)}
        </span>
      </Row>
    );
  }

  const promote = async () => {
    setBusy(true);
    try {
      await post(`/releases/${encodeURIComponent(release.id)}/promote`, {
        environment: "production",
      });
      setProblem(null);
      onPromoted();
    } catch (error: unknown) {
      if (error instanceof ApiError) setProblem(error.problem);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Row>
        <button
          className="btn"
          type="button"
          disabled={busy || release.status !== "ready"}
          title={
            release.status === "ready"
              ? release.promotion
              : "Only a version whose gate passed can be promoted."
          }
          onClick={() => void promote()}
        >
          {busy ? "Promoting…" : "Promote to production"}
        </button>
        {release.report_id ? (
          <Link className="btn btn-ghost" href={`/regression/${release.report_id}`}>
            Open the report
          </Link>
        ) : (
          <span style={{ color: "var(--text-faint)" }}>
            No regression report for this version yet, so it is unverified rather than safe.
          </span>
        )}
      </Row>
      {problem ? <Problem problem={problem} /> : null}
    </>
  );
}

export function ReleasesScreen() {
  const { data, problem, loading, reload } = useApi<ReleaseView[]>("/releases");

  if (problem)
    return (
      <div className="page">
        <Problem problem={problem} />
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
      <h1 className="page-title">Releases</h1>
      <p className="page-sub">Is it safe to deploy?</p>

      {(data ?? []).length === 0 ? (
        <Empty title="No versions yet">
          <p>
            A release is a version of an agent that has run. Set <code>version=</code> on your agent
            and each one appears here with what changed and whether its gate passed.
          </p>
        </Empty>
      ) : (
        (data ?? []).map((release) => (
          <div className="card" key={release.id} style={{ marginBottom: 10 }}>
            <Row>
              <Link href={`/agents/${release.application}`}>
                <strong>{release.application}</strong>
              </Link>
              <Tag tone="accent">v{release.version}</Tag>
              {release.previous_version ? (
                <span style={{ color: "var(--text-faint)" }}>from v{release.previous_version}</span>
              ) : null}
              <span className="spacer" />
              <Tag
                tone={
                  release.status === "ready" ? "ok" : release.status === "blocked" ? "fail" : "warn"
                }
              >
                {release.status.toUpperCase()}
              </Tag>
            </Row>

            <div className="ctx-meta" style={{ marginTop: 8 }}>
              <span>
                Runs <b>{count(release.runs)}</b>
              </span>
              <span>
                Regression tests <b>{count(release.tests)}</b>
              </span>
              <span>
                Passed <b>{count(release.passed)}</b>
              </span>
              <span>
                Failed <b>{count(release.failed)}</b>
              </span>
              {release.cost_delta_percent !== null ? (
                <span>
                  Cost{" "}
                  <b>
                    {release.cost_delta_percent > 0 ? "+" : ""}
                    {release.cost_delta_percent.toFixed(1)}%
                  </b>
                </span>
              ) : null}
              {release.created_at ? (
                <span>
                  First run <b>{ago(release.created_at)}</b>
                </span>
              ) : null}
            </div>

            {release.changed.length > 0 ? (
              <div style={{ marginTop: 8 }}>
                <div className="metric-label">Changed</div>
                <Row gap={6}>
                  {release.changed.map((component) => (
                    <Tag key={`${component.kind}:${component.name}`}>
                      {component.kind}: {component.name}
                    </Tag>
                  ))}
                </Row>
              </div>
            ) : null}

            {release.blocked_by.length > 0 ? (
              <div style={{ marginTop: 8 }}>
                <div className="metric-label">Blocked by</div>
                {release.blocked_by.map((reason) => (
                  <p key={reason} style={{ margin: "4px 0 0", color: "var(--fail)" }}>
                    {reason}
                  </p>
                ))}
              </div>
            ) : null}

            <PromoteButton release={release} onPromoted={reload} />
          </div>
        ))
      )}
    </div>
  );
}

export function ExperimentsScreen() {
  const { data, problem, loading } = useApi<ExperimentView[]>("/experiments");

  if (problem)
    return (
      <div className="page">
        <Problem problem={problem} />
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
      <h1 className="page-title">Experiments</h1>
      <p className="page-sub">Control against variants, on the same dataset.</p>

      {(data ?? []).length === 0 ? (
        <Empty title="No experiments yet">
          <p>
            Run the same dataset against two targets and they line up here: quality, success, cost,
            latency and failures, side by side.
          </p>
          <Link className="btn" href="/regression">
            Regression
          </Link>
        </Empty>
      ) : (
        (data ?? []).map((experiment) => (
          <div key={experiment.dataset} style={{ marginBottom: 18 }}>
            <h2 className="section-title">{experiment.dataset}</h2>
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Arm</th>
                  <th scope="col">Target</th>
                  <th scope="col" className="num">
                    Success
                  </th>
                  {experiment.measured.map((metric) => (
                    <th scope="col" className="num" key={metric}>
                      {metric}
                    </th>
                  ))}
                  <th scope="col" className="num">
                    Cost
                  </th>
                  <th scope="col" className="num">
                    Latency
                  </th>
                  <th scope="col" className="num">
                    Failures
                  </th>
                </tr>
              </thead>
              <tbody>
                {experiment.variants.map((variant) => (
                  <tr key={variant.report_id} style={{ cursor: "default" }}>
                    <td>
                      <Link href={`/regression/${variant.report_id}`}>{variant.label}</Link>
                      {variant.winner ? (
                        <>
                          {" "}
                          <Tag tone="ok">best</Tag>
                        </>
                      ) : null}
                    </td>
                    <td className="mono">{variant.target || "—"}</td>
                    <td className="num">{percent(variant.success_rate)}</td>
                    {experiment.measured.map((metric) => (
                      <td className="num" key={metric}>
                        {variant.metrics[metric] === undefined
                          ? "—"
                          : variant.metrics[metric]!.toFixed(2)}
                      </td>
                    ))}
                    <td className="num">{money(variant.avg_cost)}</td>
                    <td className="num">{duration(variant.avg_latency_ms)}</td>
                    <td className="num">{count(variant.failures)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))
      )}
    </div>
  );
}

export function NotificationsScreen() {
  const { data, problem, loading } = useApi<NotificationView[]>("/notifications");

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

  return (
    <div className="page page-narrow">
      <h1 className="page-title">Notifications</h1>
      <p className="page-sub">Meaningful AI events, one per cause.</p>

      {(data ?? []).length === 0 ? (
        <Empty title="Nothing to report">
          <p>
            Regressions, drift, cost spikes, dependency changes and failure clusters appear here.
            Silence means none of them happened.
          </p>
        </Empty>
      ) : (
        (data ?? []).map((notification) => (
          <div className="list-row" key={notification.id}>
            <Tag
              tone={
                notification.severity === "critical"
                  ? "fail"
                  : notification.severity === "warning"
                    ? "warn"
                    : "default"
              }
            >
              {notification.kind}
            </Tag>
            <span>
              {notification.summary}
              {notification.detail ? (
                <span style={{ color: "var(--text-faint)" }}> — {notification.detail}</span>
              ) : null}
            </span>
            <span className="spacer" />
            <span style={{ color: "var(--text-faint)" }}>{ago(notification.at)}</span>
            {notification.href ? (
              <Link className="btn btn-ghost" href={notification.href}>
                Inspect
              </Link>
            ) : null}
          </div>
        ))
      )}
    </div>
  );
}
