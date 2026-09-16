"use client";

/**
 * The remaining run-detail panels (UI spec §14-§19).
 *
 * Each one fetches only its own projection, when its tab is opened (UI §50),
 * and shows what its section of the spec asks for -- no more, so the panel
 * stays scannable.
 */

import { useState } from "react";

import type {
  ApprovalView,
  GuardrailView,
  McpView,
  MemoryView,
  ModelView,
  PromptView,
  SkillsView,
  ToolView,
} from "@/api/types";
import { useApi } from "@/hooks/useApi";
import { ago, duration, json, money, tokens, truncate } from "@/lib/format";
import {
  Code,
  Empty,
  Field,
  Problem,
  Row,
  Skeleton,
  StatusDot,
  Tag,
} from "@/components/common/atoms";

function Panel<T>({
  path,
  empty,
  children,
}: {
  path: string;
  empty: { title: string; body: string };
  children: (data: T) => React.ReactNode;
}) {
  const { data, problem, loading, reload } = useApi<T>(path);
  if (problem) return <Problem problem={problem} onRetry={reload} />;
  if (loading && !data) return <Skeleton rows={6} />;
  if (!data) return null;
  const rendered = children(data);
  if (Array.isArray(rendered) && rendered.length === 0) {
    return <Empty title={empty.title}>{empty.body}</Empty>;
  }
  return <>{rendered}</>;
}

export function ModelPanel({ runId }: { runId: string }) {
  return (
    <Panel<ModelView>
      path={`/runs/${runId}/model`}
      empty={{ title: "No model calls", body: "This run never reached a model." }}
    >
      {(data) =>
        data.calls.length === 0 ? (
          []
        ) : (
          <div>
            <Row>
              <Tag tone="accent">{data.providers.join(", ") || "provider"}</Tag>
              <Tag>{data.models.join(", ")}</Tag>
              <span className="spacer" />
              <span style={{ color: "var(--text-muted)" }}>
                {money(data.total_cost)} · {duration(data.total_latency_ms)}
              </span>
            </Row>
            {data.calls.map((call) => (
              <div className="card" key={call.index} style={{ marginTop: 10 }}>
                <Row>
                  <strong className="mono">{call.model}</strong>
                  <Tag>{call.provider}</Tag>
                  {call.error ? <Tag tone="fail">{call.error}</Tag> : null}
                  <span className="spacer" />
                  <span className="mono" style={{ color: "var(--text-faint)" }}>
                    #{call.index + 1}
                  </span>
                </Row>
                <div className="ctx-meta" style={{ marginTop: 8, gap: 18 }}>
                  <Field label="Temperature">{call.temperature ?? "default"}</Field>
                  <Field label="Max tokens">{call.max_tokens ?? "default"}</Field>
                  <Field label="Input">{tokens(call.input_tokens)}</Field>
                  <Field label="Output">{tokens(call.output_tokens)}</Field>
                  <Field label="Cached">{tokens(call.cached_tokens)}</Field>
                  <Field label="Reasoning">{tokens(call.reasoning_tokens)}</Field>
                  <Field label="Cost">
                    {money(call.cost)}{" "}
                    <span style={{ color: "var(--text-faint)" }}>({call.cost_source})</span>
                  </Field>
                  <Field label="Latency">{duration(call.latency_ms)}</Field>
                  <Field label="Finish">{call.finish_reason ?? "—"}</Field>
                </div>
                {call.text ? (
                  <Code label={`Response text for call ${call.index + 1}`} maxHeight={260}>
                    {truncate(call.text, 1200)}
                  </Code>
                ) : null}
              </div>
            ))}
          </div>
        )
      }
    </Panel>
  );
}

export function PromptPanel({ runId }: { runId: string }) {
  const [index, setIndex] = useState(0);
  return (
    <Panel<PromptView>
      path={`/runs/${runId}/prompt`}
      empty={{ title: "No prompts", body: "Nothing was sent to a model in this run." }}
    >
      {(data) => {
        if (data.calls.length === 0) return [];
        const call = data.calls[Math.min(index, data.calls.length - 1)]!;
        const sections: [string, typeof call.system][] = [
          ["System", call.system],
          ["Developer", call.developer],
          ["User", call.user],
          ["Assistant", call.assistant],
          ["Tool", call.tool],
          ["Tool instructions", call.tool_instructions],
          ["Skill instructions", call.skill_instructions],
        ];
        return (
          <div>
            <Row>
              {data.calls.map((candidate, position) => (
                <button
                  key={candidate.index}
                  type="button"
                  className={position === index ? "btn btn-primary" : "btn btn-ghost"}
                  onClick={() => setIndex(position)}
                >
                  Call {position + 1}
                </button>
              ))}
              <span className="spacer" />
              {call.fingerprint ? (
                <span className="mono" style={{ color: "var(--text-faint)" }}>
                  {call.fingerprint.slice(0, 20)}
                </span>
              ) : null}
            </Row>
            {sections.map(([label, messages]) =>
              messages.length === 0 ? null : (
                <div key={label} style={{ marginTop: 12 }}>
                  <h3 className="section-title" style={{ marginTop: 0 }}>
                    {label}
                  </h3>
                  {messages.map((message, position) => (
                    <div
                      className="card"
                      key={`${message.hash}:${position}`}
                      style={{ marginBottom: 8 }}
                    >
                      <Row>
                        {message.name ? <Tag>{message.name}</Tag> : null}
                        <span className="spacer" />
                        <span className="mono" style={{ color: "var(--text-faint)" }}>
                          {message.hash.replace("sha256:", "")}
                        </span>
                      </Row>
                      <pre
                        style={{
                          margin: "8px 0 0",
                          whiteSpace: "pre-wrap",
                          wordBreak: "break-word",
                          fontFamily: "var(--font-mono)",
                          fontSize: "var(--size-sm)",
                        }}
                      >
                        {message.text || "(empty)"}
                      </pre>
                    </div>
                  ))}
                </div>
              ),
            )}
          </div>
        );
      }}
    </Panel>
  );
}

export function MemoryPanel({ runId }: { runId: string }) {
  return (
    <Panel<MemoryView>
      path={`/runs/${runId}/memory`}
      empty={{ title: "No memory operations", body: "This run neither read nor wrote memory." }}
    >
      {(data) =>
        data.operations.length === 0
          ? []
          : data.operations.map((operation) => (
              <div
                className="card"
                key={`${operation.seq}:${operation.id ?? operation.content}`}
                style={{ marginBottom: 8 }}
              >
                <Row>
                  <Tag tone={operation.operation === "write" ? "accent" : "default"}>
                    {operation.operation === "write" ? "Memory write" : "Memory read"}
                  </Tag>
                  <Tag>{operation.kind}</Tag>
                  <span className="spacer" />
                  <span style={{ color: "var(--text-faint)" }}>{ago(operation.timestamp)}</span>
                </Row>
                <p style={{ margin: "8px 0 0" }}>{operation.content}</p>
                <div className="ctx-meta">
                  {operation.query ? (
                    <span>
                      Query <b>{operation.query}</b>
                    </span>
                  ) : null}
                  {operation.score !== null ? (
                    <span>
                      Score <b>{operation.score.toFixed(2)}</b>
                    </span>
                  ) : null}
                  {operation.confidence !== null ? (
                    <span>
                      Confidence <b>{operation.confidence.toFixed(2)}</b>
                    </span>
                  ) : null}
                  {operation.importance !== null ? (
                    <span>
                      Importance <b>{operation.importance.toFixed(2)}</b>
                    </span>
                  ) : null}
                  {operation.created_at ? (
                    <span>
                      Created <b>{ago(operation.created_at)}</b>
                    </span>
                  ) : null}
                  {operation.provider ? (
                    <span>
                      Provider <b>{operation.provider}</b>
                    </span>
                  ) : null}
                </div>
              </div>
            ))
      }
    </Panel>
  );
}

export function ToolsPanel({ runId }: { runId: string }) {
  return (
    <Panel<ToolView>
      path={`/runs/${runId}/tools`}
      empty={{ title: "No tool calls", body: "The model answered without calling a tool." }}
    >
      {(data) =>
        data.calls.length === 0
          ? []
          : data.calls.map((call) => (
              <div className="card" key={call.tool_call_id} style={{ marginBottom: 8 }}>
                <Row>
                  <StatusDot status={call.status} />
                  <strong className="mono">{call.name}()</strong>
                  {call.server ? <Tag tone="accent">MCP · {call.server}</Tag> : null}
                  {call.version ? <Tag>v{call.version}</Tag> : null}
                  {call.status === "denied" ? <Tag tone="fail">denied by policy</Tag> : null}
                  <span className="spacer" />
                  <span style={{ color: "var(--text-faint)" }}>{duration(call.duration_ms)}</span>
                  <button
                    className="btn btn-ghost"
                    type="button"
                    disabled
                    title={
                      call.replayable
                        ? "Replaying a tool call arrives with the replay workspace (P1)."
                        : "This tool declares side effects, so it is not safe to replay."
                    }
                  >
                    Replay tool call
                  </button>
                </Row>
                <div
                  style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 8 }}
                >
                  <div>
                    <div className="metric-label">Arguments</div>
                    <Code label={`Arguments for ${call.name}`}>{json(call.arguments)}</Code>
                  </div>
                  <div>
                    <div className="metric-label">Response</div>
                    <Code label={`Response from ${call.name}`}>
                      {truncate(json(call.result), 1500) || "(none)"}
                    </Code>
                  </div>
                </div>
              </div>
            ))
      }
    </Panel>
  );
}

export function McpPanel({ runId }: { runId: string }) {
  return (
    <Panel<McpView>
      path={`/runs/${runId}/mcp`}
      empty={{ title: "No MCP servers", body: "This run used no Model Context Protocol server." }}
    >
      {(data) =>
        data.servers.length === 0
          ? []
          : data.servers.map((server) => (
              <div className="card" key={server.server} style={{ marginBottom: 10 }}>
                <Row>
                  <strong>{server.server}</strong>
                  <Tag tone="accent">v{server.version}</Tag>
                  {server.transport ? <Tag>{server.transport}</Tag> : null}
                  {server.changed_since_previous_run ? (
                    <Tag tone="warn">
                      changed since previous run (was v{server.previous_version})
                    </Tag>
                  ) : null}
                  <span className="spacer" />
                  <span style={{ color: "var(--text-faint)" }}>
                    connected in {duration(server.latency_ms)}
                  </span>
                </Row>
                <div style={{ marginTop: 8 }}>
                  <div className="metric-label">Tools</div>
                  <Row gap={6}>
                    {server.tools.map((tool) => (
                      <Tag key={tool.name} title={tool.fingerprint ?? undefined}>
                        {tool.name}
                        {tool.calls > 0
                          ? ` · ${tool.calls} call${tool.calls === 1 ? "" : "s"}`
                          : ""}
                      </Tag>
                    ))}
                  </Row>
                </div>
                {server.resources.length > 0 ? (
                  <div style={{ marginTop: 8 }}>
                    <div className="metric-label">Resources</div>
                    <Row gap={6}>
                      {server.resources.map((resource) => (
                        <Tag key={resource}>{resource}</Tag>
                      ))}
                    </Row>
                  </div>
                ) : null}
              </div>
            ))
      }
    </Panel>
  );
}

const SKILL_STATE: Record<string, "default" | "accent" | "ok"> = {
  discovered: "default",
  loaded: "accent",
  used: "ok",
};

export function SkillsPanel({ runId }: { runId: string }) {
  return (
    <Panel<SkillsView>
      path={`/runs/${runId}/skills`}
      empty={{ title: "No skills", body: "No skill was discovered, loaded or used." }}
    >
      {(data) =>
        data.skills.length === 0
          ? []
          : data.skills.map((skill) => (
              <div className="card" key={skill.skill} style={{ marginBottom: 8 }}>
                <Row>
                  <strong>{skill.skill}</strong>
                  <Tag>v{skill.version}</Tag>
                  <Tag tone={SKILL_STATE[skill.state] ?? "default"}>
                    {skill.state.toUpperCase()}
                  </Tag>
                  {skill.activations > 1 ? <Tag>{skill.activations} activations</Tag> : null}
                </Row>
                {skill.description ? (
                  <p style={{ margin: "6px 0 0", color: "var(--text-muted)" }}>
                    {skill.description}
                  </p>
                ) : null}
                <div className="ctx-meta">
                  {skill.resources.length > 0 ? (
                    <span>
                      Resources <b>{skill.resources.join(", ")}</b>
                    </span>
                  ) : null}
                  {skill.scripts.length > 0 ? (
                    <span>
                      Scripts <b>{skill.scripts.join(", ")}</b>
                    </span>
                  ) : null}
                  {skill.allowed_tools.length > 0 ? (
                    <span>
                      Tools <b>{skill.allowed_tools.join(", ")}</b>
                    </span>
                  ) : null}
                </div>
              </div>
            ))
      }
    </Panel>
  );
}

export function DecisionsPanel({ runId }: { runId: string }) {
  const approvals = useApi<ApprovalView[]>(`/runs/${runId}/approvals`);
  const guardrails = useApi<GuardrailView[]>(`/runs/${runId}/guardrails`);
  if (approvals.loading || guardrails.loading) return <Skeleton rows={4} />;
  const decisions = approvals.data ?? [];
  const checks = guardrails.data ?? [];
  if (decisions.length === 0 && checks.length === 0) {
    return (
      <Empty title="No policy decisions">
        No guardrail ran and no human approval was requested in this run.
      </Empty>
    );
  }
  return (
    <div>
      {decisions.map((approval) => (
        <div className="card" key={approval.request_id} style={{ marginBottom: 8 }}>
          <Row>
            <Tag
              tone={
                approval.decision === "approved"
                  ? "ok"
                  : approval.decision === "rejected"
                    ? "fail"
                    : "warn"
              }
            >
              {approval.decision}
            </Tag>
            <strong>{approval.action}</strong>
            {approval.risk ? <Tag tone="warn">risk: {approval.risk}</Tag> : null}
            <span className="spacer" />
            <span style={{ color: "var(--text-faint)" }}>
              {approval.by ? `by ${approval.by} · ` : ""}
              {ago(approval.decided_at ?? approval.requested_at)}
            </span>
          </Row>
          {approval.reason ? <p style={{ margin: "6px 0 0" }}>{approval.reason}</p> : null}
          <Code label={`Details for ${approval.action}`} maxHeight={180}>
            {json(approval.details)}
          </Code>
        </div>
      ))}
      {checks.map((check) => (
        <div className="card" key={check.seq} style={{ marginBottom: 8 }}>
          <Row>
            <Tag tone={check.triggered ? "warn" : "ok"}>
              {check.triggered ? "triggered" : "passed"}
            </Tag>
            <strong>{check.guardrail || "guardrail"}</strong>
            {check.stage ? <Tag>{check.stage}</Tag> : null}
            {check.action ? <Tag tone="fail">{check.action}</Tag> : null}
          </Row>
        </div>
      ))}
    </div>
  );
}
