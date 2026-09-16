"use client";

/**
 * Live mode and approvals (UI spec §34, §35).
 *
 * A running agent writes its events as it goes, so this is a view of files
 * being appended to in another process. Nothing is simulated: when the stream
 * says a node is running, a node is running.
 */

import { useCallback, useState } from "react";

import { post } from "@/api/client";
import type { Capabilities, LiveFrame, LiveRun, PendingApprovalView } from "@/api/types";
import { useApi } from "@/hooks/useApi";
import { useStream } from "@/hooks/useStream";
import { ago, json, money, tokens } from "@/lib/format";
import { Link } from "@/lib/router";
import { Code, Empty, Problem, Row, Skeleton, StatusDot, Tag } from "@/components/common/atoms";

function Running({ run }: { run: LiveRun }) {
  return (
    <div className="card" style={{ marginBottom: 10 }}>
      <Row>
        <StatusDot status="running" label="running" />
        <Link className="mono" href={`/runs/${run.run.id}`}>
          {run.run.id}
        </Link>
        <strong>{run.run.agent ?? run.run.name}</strong>
        <Tag tone="accent">{run.stage}</Tag>
        <span className="spacer" />
        <span style={{ color: "var(--text-muted)" }}>
          {tokens(run.context_tokens)} context · {money(run.cost)} · {run.events} events
        </span>
      </Row>
      {run.nodes.length > 0 ? (
        <Row gap={6}>
          {run.nodes.map((node) => (
            <Tag
              key={node.id}
              tone={node.status === "error" ? "fail" : node.status === "ok" ? "ok" : "warn"}
            >
              {node.label}
              {node.status === "running" ? " ●" : node.status === "ok" ? " ✓" : ""}
            </Tag>
          ))}
        </Row>
      ) : null}
    </div>
  );
}

function Approvals() {
  const { data, reload } = useApi<PendingApprovalView[]>("/approvals");
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<{ error: string; detail: string } | null>(null);

  const decide = useCallback(
    async (requestId: string, approved: boolean, reason = "") => {
      setBusy(requestId);
      setProblem(null);
      try {
        await post(`/approvals/${requestId}/decision`, { approved, by: "console", reason });
        reload();
      } catch (error) {
        const detail = (error as { problem?: { error: string; detail: string } }).problem;
        setProblem(detail ?? { error: "Could not decide", detail: String(error) });
      } finally {
        setBusy(null);
      }
    },
    [reload],
  );

  const pending = (data ?? []).filter((item) => item.decision === "pending" && !item.expired);
  const decided = (data ?? []).filter((item) => item.decision !== "pending" || item.expired);

  return (
    <div>
      <h2 className="section-title">Approvals</h2>
      {problem ? <Problem problem={{ ...problem, action: null, href: null }} /> : null}
      {pending.length === 0 ? (
        <p style={{ color: "var(--text-muted)" }}>
          Nothing is waiting for a person. An agent using <code>InboxHandler()</code> asks here and
          waits for the answer.
        </p>
      ) : (
        pending.map((item) => (
          <div className="card" key={item.request_id} style={{ marginBottom: 10 }}>
            <Row>
              <Tag tone="warn">approval required</Tag>
              <strong>{item.action}</strong>
              <Tag>risk: {item.risk}</Tag>
              <span className="spacer" />
              <span style={{ color: "var(--text-faint)" }}>{ago(item.requested_at)}</span>
            </Row>
            <Code label={`Details for ${item.action}`} maxHeight={160}>
              {json(item.details)}
            </Code>
            <Row>
              <button
                className="btn btn-primary"
                type="button"
                disabled={busy === item.request_id}
                onClick={() => decide(item.request_id, true)}
              >
                Approve
              </button>
              <button
                className="btn"
                type="button"
                disabled={busy === item.request_id}
                onClick={() => decide(item.request_id, false, "rejected from the console")}
              >
                Reject
              </button>
              <button
                className="btn btn-ghost"
                type="button"
                disabled={busy === item.request_id}
                onClick={() =>
                  decide(item.request_id, false, "changes requested: see the run for details")
                }
              >
                Request changes
              </button>
              {item.run_id ? (
                <Link className="btn btn-ghost" href={`/runs/${item.run_id}`}>
                  Open the run
                </Link>
              ) : null}
            </Row>
          </div>
        ))
      )}

      {decided.length > 0 ? (
        <table className="table" style={{ marginTop: 10 }}>
          <thead>
            <tr>
              <th scope="col">Action</th>
              <th scope="col">Decision</th>
              <th scope="col">By</th>
              <th scope="col">Reason</th>
              <th scope="col">When</th>
            </tr>
          </thead>
          <tbody>
            {decided.map((item) => (
              <tr key={item.request_id} style={{ cursor: "default" }}>
                <td>{item.action}</td>
                <td>
                  <Tag tone={item.decision === "approved" ? "ok" : "fail"}>
                    {item.expired && item.decision === "pending" ? "expired" : item.decision}
                  </Tag>
                </td>
                <td>{item.by ?? "—"}</td>
                <td>{item.reason || "—"}</td>
                <td>{ago(item.requested_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}

export function LiveScreen({ capabilities }: { capabilities: Capabilities | null }) {
  const { frame, connected, error } = useStream<LiveFrame>("/live");
  const snapshot = useApi<LiveRun[]>(frame === null ? "/live/runs" : null);
  const runs = frame?.runs ?? snapshot.data ?? [];
  // Approvals are answered where the agent is waiting, which is not always
  // where the console is. The surface says whether it holds the queue.
  const holdsApprovals = capabilities?.features.includes("approvals") ?? false;

  return (
    <div className="page">
      <Row>
        <h1 className="page-title" style={{ margin: 0 }}>
          Live
        </h1>
        <Tag tone={connected ? "ok" : "warn"}>{connected ? "streaming" : "connecting…"}</Tag>
      </Row>
      <p className="page-sub">What is running right now.</p>
      {error ? <p style={{ color: "var(--warn)" }}>{error}</p> : null}

      {snapshot.loading && !frame ? <Skeleton rows={4} /> : null}
      {runs.length === 0 ? (
        <Empty title="Nothing is running">
          <p>
            Start an agent and it appears here while it works &mdash; the stage it is in, the
            context it has assembled, and what it has spent so far.
          </p>
          <Link className="btn" href="/runs">
            Recent runs
          </Link>
        </Empty>
      ) : (
        runs.map((run) => <Running key={run.run.id} run={run} />)
      )}

      {holdsApprovals ? (
        <Approvals />
      ) : (
        <>
          <h2 className="section-title">Approvals</h2>
          <p style={{ color: "var(--text-muted)" }}>
            An agent waiting for approval is waiting on the machine it runs on, so the queue lives
            in the local console there: <code>rewyn ui</code>.
          </p>
        </>
      )}
    </div>
  );
}
