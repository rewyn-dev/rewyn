"use client";

/**
 * The AI development workspace, and the loop it closes (UI spec §37, §60, §62).
 *
 * §37 draws one column and asks the console to feel like an IDE. The thing
 * that actually makes an IDE feel like an IDE is not the layout, it is that
 * every pane knows the state of the thing it is showing -- so each band here
 * carries its own count, its own status and, when it has never happened, the
 * sentence that says what would start it.
 *
 * §60's loop runs along the top as a strip rather than a diagram. A diagram
 * of a lifecycle is decoration; a strip of nine steps you can click, each one
 * coloured by where this agent actually stands, is the navigation the gate
 * asks for.
 */

import type { WorkspaceStage, WorkspaceView } from "@/api/types";
import { Comments } from "@/components/common/Comments";
import { Empty, Problem, Row, Skeleton, Tag } from "@/components/common/atoms";
import { RunsTable } from "@/components/runs/RunsTable";
import { useApi } from "@/hooks/useApi";
import { Link, useRouter } from "@/lib/router";

const KIND_ORDER = [
  "model",
  "prompt",
  "skill",
  "tool",
  "mcp_server",
  "context",
  "memory",
  "retriever",
  "guardrail",
  "embedding_model",
];

function tone(status: WorkspaceStage["status"]): "ok" | "warn" | "fail" | "default" {
  if (status === "ok") return "ok";
  if (status === "warn") return "warn";
  if (status === "fail") return "fail";
  return "default";
}

function LoopStrip({ steps }: { steps: WorkspaceStage[] }) {
  return (
    <nav className="loop" aria-label="Development loop">
      {steps.map((step, index) => (
        <Link
          className="loop-step"
          key={step.key}
          href={step.href}
          data-status={step.status}
          title={`${step.question} — ${step.summary || step.detail}`}
        >
          <span className="loop-index" aria-hidden="true">
            {index + 1}
          </span>
          <span className="loop-label">{step.label}</span>
          <span className="loop-state">{step.summary || "—"}</span>
        </Link>
      ))}
      <span className="loop-return" aria-hidden="true">
        ↻
      </span>
    </nav>
  );
}

function Band({ stage }: { stage: WorkspaceStage }) {
  return (
    <Link className="band" href={stage.href} data-status={stage.status} data-ready={stage.ready}>
      <div className="band-head">
        <strong>{stage.label}</strong>
        {stage.count !== null ? <span className="count">{stage.count}</span> : null}
        <span className="spacer" />
        <Tag tone={tone(stage.status)}>{stage.status}</Tag>
      </div>
      <div className="band-question">{stage.question}</div>
      <div className="band-state">{stage.summary || stage.detail}</div>
    </Link>
  );
}

function AgentPicker({ value }: { value: string }) {
  const { navigate } = useRouter();
  const { data } = useApi<{ name: string }[]>("/agents");
  return (
    <Row>
      <label htmlFor="workspace-agent">Agent</label>
      <select
        id="workspace-agent"
        className="select"
        value={value}
        onChange={(event) => navigate(`/workspace?agent=${encodeURIComponent(event.target.value)}`)}
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

export function WorkspaceScreen({ agent }: { agent: string | null }) {
  const { data, problem, loading } = useApi<WorkspaceView>(
    agent ? "/workspace" : null,
    agent ? { agent } : undefined,
  );

  if (!agent)
    return (
      <div className="page">
        <h1 className="page-title">Workspace</h1>
        <p className="page-sub">One agent, from its configuration to its release.</p>
        <AgentPicker value="" />
        <Empty title="Pick an agent">
          <p>
            The workspace puts one agent&apos;s whole lifecycle in a single frame: what it is made
            of, what it last did, what it used, whether it reproduces, whether it is good, and
            whether it got worse.
          </p>
          <Link className="btn" href="/agents">
            Browse agents
          </Link>
        </Empty>
      </div>
    );

  if (problem)
    return (
      <div className="page">
        <AgentPicker value={agent} />
        <Problem problem={problem} />
      </div>
    );
  if (loading && !data)
    return (
      <div className="page">
        <Skeleton rows={10} />
      </div>
    );
  if (!data) return null;

  const grouped = new Map<string, typeof data.components>();
  for (const component of data.components) {
    grouped.set(component.kind, [...(grouped.get(component.kind) ?? []), component]);
  }
  const kinds = [...grouped.keys()].sort(
    (a, b) =>
      (KIND_ORDER.indexOf(a) + 1 || 99) - (KIND_ORDER.indexOf(b) + 1 || 99) || a.localeCompare(b),
  );

  return (
    <div className="page">
      <Row>
        <AgentPicker value={agent} />
        <Tag tone="accent">v{data.version}</Tag>
        <span className="spacer" />
        <Link className="btn btn-ghost" href={`/agents/${encodeURIComponent(data.agent)}`}>
          Agent page
        </Link>
      </Row>

      <h1 className="page-title" style={{ marginTop: 10 }}>
        {data.agent}
      </h1>
      <p className="page-sub">{data.next_step}</p>

      <LoopStrip steps={data.loop} />

      {data.incidents.length > 0 ? (
        <div className="card" style={{ borderColor: "var(--fail)", marginBottom: 12 }}>
          {data.incidents.map((incident) => (
            <Row key={incident.id}>
              <Tag tone="fail">incident</Tag>
              <Link href={`/incidents/${incident.id}`}>{incident.summary}</Link>
              <span className="spacer" />
              <span style={{ color: "var(--text-faint)" }}>{incident.affected_runs} runs</span>
            </Row>
          ))}
        </div>
      ) : null}

      <div className="workspace">
        <section className="workspace-config" aria-labelledby="config">
          <h2 className="section-title" id="config" style={{ marginTop: 0 }}>
            Configuration
          </h2>
          {kinds.length === 0 ? (
            <p style={{ color: "var(--text-faint)" }}>
              No run of this agent recorded what it was made of.
            </p>
          ) : (
            kinds.map((kind) => (
              <div key={kind} style={{ marginBottom: 8 }}>
                <div className="metric-label">{kind.replace("_", " ")}</div>
                {(grouped.get(kind) ?? []).map((component) => (
                  <div className="list-row" key={`${component.kind}:${component.name}`}>
                    {component.href ? (
                      <Link href={component.href}>{component.name}</Link>
                    ) : (
                      <span>{component.name}</span>
                    )}
                    <span className="spacer" />
                    {component.version !== "unversioned" ? (
                      <span className="count">v{component.version}</span>
                    ) : null}
                  </div>
                ))}
              </div>
            ))
          )}
        </section>

        <section className="workspace-stages" aria-labelledby="stages">
          <h2 className="section-title" id="stages" style={{ marginTop: 0 }}>
            Lifecycle
          </h2>
          {data.stages.map((stage) => (
            <Band key={stage.key} stage={stage} />
          ))}
        </section>
      </div>

      <h2 className="section-title">Recent runs</h2>
      <RunsTable runs={data.recent_runs} />

      <div style={{ marginTop: 14 }}>
        <Comments subject={`agent:${data.agent}`} title="Notes on this agent" />
      </div>
    </div>
  );
}
