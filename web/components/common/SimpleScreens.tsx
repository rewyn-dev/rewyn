"use client";

/** Sessions, environments and settings: small screens the navigation owes. */

import type { Capabilities, EnvironmentView, SessionView } from "@/api/types";
import { useApi } from "@/hooks/useApi";
import { ago, count, duration, money } from "@/lib/format";
import { Link } from "@/lib/router";
import { Empty, Problem, Skeleton, Tag } from "./atoms";

export function SessionsScreen() {
  const { data, problem, loading, reload } = useApi<SessionView[]>("/sessions");
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
  return (
    <div className="page page-narrow">
      <h1 className="page-title">Sessions</h1>
      <p className="page-sub">Runs that belong to one conversation.</p>
      {(data ?? []).length === 0 ? (
        <Empty title="No sessions yet">
          <p>
            A session is any set of runs sharing a <code>session_id</code>. Record one and they
            group here:
          </p>
          <pre>{`start_run("support", metadata={"session_id": "sess-1"})`}</pre>
        </Empty>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Session</th>
              <th scope="col">Agents</th>
              <th scope="col">User</th>
              <th scope="col" className="num">
                Runs
              </th>
              <th scope="col" className="num">
                Failures
              </th>
              <th scope="col" className="num">
                Cost
              </th>
              <th scope="col" className="num">
                Duration
              </th>
              <th scope="col">Started</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((session) => (
              <tr key={session.id}>
                <td className="mono">
                  <Link href={`/runs?session_id=${encodeURIComponent(session.id)}`}>
                    {session.id}
                  </Link>
                </td>
                <td>{session.agents.join(", ") || "—"}</td>
                <td>{session.user ?? "—"}</td>
                <td className="num">{count(session.runs)}</td>
                <td className="num">{count(session.failures)}</td>
                <td className="num">{money(session.cost)}</td>
                <td className="num">
                  {session.ended_at
                    ? duration(
                        new Date(session.ended_at).getTime() -
                          new Date(session.started_at).getTime(),
                      )
                    : "—"}
                </td>
                <td>{ago(session.started_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function EnvironmentsScreen() {
  const { data, loading } = useApi<EnvironmentView[]>("/environments");
  if (loading && !data)
    return (
      <div className="page">
        <Skeleton rows={4} />
      </div>
    );
  return (
    <div className="page page-narrow">
      <h1 className="page-title">Environments</h1>
      <p className="page-sub">Every run carries the environment it ran in.</p>
      {(data ?? []).length === 0 ? (
        <Empty title="No environments recorded">
          <p>Tag a run with its environment and it is filterable everywhere:</p>
          <pre>{`start_run("agent", metadata={"environment": "production"})`}</pre>
        </Empty>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Environment</th>
              <th scope="col" className="num">
                Runs
              </th>
              <th scope="col">Last run</th>
              <th scope="col" />
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((environment) => (
              <tr key={environment.name}>
                <td>
                  <Tag tone={environment.name === "production" ? "accent" : "default"}>
                    {environment.name}
                  </Tag>
                </td>
                <td className="num">{count(environment.runs)}</td>
                <td>{ago(environment.last_run_at)}</td>
                <td>
                  <Link className="btn btn-ghost" href={`/runs?environment=${environment.name}`}>
                    Open runs
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function SettingsScreen({ capabilities }: { capabilities: Capabilities | null }) {
  return (
    <div className="page page-narrow">
      <h1 className="page-title">Settings</h1>
      <p className="page-sub">What this console is connected to.</p>
      <div className="card">
        <table className="table">
          <tbody>
            <tr>
              <td>Surface</td>
              <td className="mono">{capabilities?.surface ?? "…"}</td>
            </tr>
            <tr>
              <td>Project</td>
              <td className="mono">{capabilities?.project ?? "…"}</td>
            </tr>
            <tr>
              <td>Role</td>
              <td className="mono">{capabilities?.role ?? "…"}</td>
            </tr>
            <tr>
              <td>SDK version</td>
              <td className="mono">{capabilities?.sdk_version ?? "…"}</td>
            </tr>
            <tr>
              <td>API version</td>
              <td className="mono">{capabilities?.api_version ?? "…"}</td>
            </tr>
            <tr>
              <td>Features</td>
              <td>{(capabilities?.features ?? []).join(", ")}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p style={{ color: "var(--text-muted)", marginTop: 12 }}>
        Keyboard: <kbd>⌘K</kbd> search · <kbd>⌘P</kbd> commands · <kbd>T</kbd> timeline ·{" "}
        <kbd>G</kbd> graph · <kbd>C</kbd> context · <kbd>O</kbd> tools
      </p>
    </div>
  );
}
