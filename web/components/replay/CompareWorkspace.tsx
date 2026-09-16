"use client";

/**
 * The compare workspace (UI spec §13, §22, §23).
 *
 * WHAT CHANGED? first, because that is the question. Then the observed
 * differences, and only then the hypotheses -- labelled Inference, carrying
 * their confidence, and never merged with the facts they are drawn from.
 *
 * "Explain difference" (§23) adds prose on top of that, and keeps the same
 * two labels. It is written from the differences above and nothing else, so
 * a sentence here can always be traced back to a row up there.
 */

import { useState } from "react";

import { ApiError, post } from "@/api/client";
import type { DiffView, NarrativeView, ProblemDetail, RunSummary } from "@/api/types";
import { useApi } from "@/hooks/useApi";
import { clock, duration, json, money, percent, truncate } from "@/lib/format";
import { Link, useRouter } from "@/lib/router";
import { Empty, Problem, Row, Skeleton, StatusDot, Tag } from "@/components/common/atoms";

/** "mcp" is an initialism; CSS capitalisation would render it "Mcp". */
function label(dimension: string): string {
  return dimension === "mcp" ? "MCP" : dimension[0]!.toUpperCase() + dimension.slice(1);
}

function Delta({ value, invert = false }: { value: number | null; invert?: boolean }) {
  if (value === null) return <span style={{ color: "var(--text-faint)" }}>—</span>;
  const rounded = Math.round(value * 10) / 10;
  if (rounded === 0) return <span>no change</span>;
  const good = invert ? rounded < 0 : rounded > 0;
  return (
    <span style={{ color: good ? "var(--ok)" : "var(--fail)" }}>
      {rounded > 0 ? "+" : ""}
      {rounded}%
    </span>
  );
}

function Picker({ runId }: { runId: string }) {
  const { navigate } = useRouter();
  const { data, loading } = useApi<RunSummary[]>(`/runs/${runId}/comparable`);
  if (loading && !data) return <Skeleton rows={4} />;
  if (!data || data.length === 0) {
    return (
      <Empty title="Nothing to compare against">
        <p>Comparison needs a second run of the same agent. Record another and it appears here.</p>
      </Empty>
    );
  }
  return (
    <div>
      <p className="page-sub">Compare with an earlier run of this agent.</p>
      <table className="table">
        <tbody>
          {data.map((candidate) => (
            <tr
              key={candidate.id}
              onClick={() => navigate(`/compare?a=${candidate.id}&b=${runId}`)}
            >
              <td style={{ width: 28 }}>
                <StatusDot status={candidate.status} />
              </td>
              <td className="mono">{candidate.id}</td>
              <td>{clock(candidate.started_at)}</td>
              <td>{candidate.user ?? "—"}</td>
              <td className="num">{money(candidate.cost)}</td>
              <td>
                {candidate.status === "succeeded" ? (
                  <Tag tone="ok">previous successful run</Tag>
                ) : (
                  <Tag tone="fail">failed</Tag>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CompareWorkspace({ a, b }: { a: string | null; b: string | null }) {
  const [open, setOpen] = useState<string | null>(null);
  const ready = Boolean(a && b);
  const { data, problem, loading, reload } = useApi<DiffView>(
    ready ? `/runs/${b}/diff` : null,
    ready ? { against: a } : undefined,
  );

  if (!b) {
    return (
      <div className="page">
        <h1 className="page-title">Compare</h1>
        <Empty title="Open a run first">
          <p>
            Comparison starts from a run: open one and press <kbd>C</kbd>, or use the Compare button
            in its header.
          </p>
          <Link className="btn" href="/runs">
            Browse runs
          </Link>
        </Empty>
      </div>
    );
  }
  if (!a) {
    return (
      <div className="page">
        <h1 className="page-title">Compare</h1>
        <Picker runId={b} />
      </div>
    );
  }
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

  return (
    <div className="page">
      <Row>
        <h1 className="page-title" style={{ margin: 0 }}>
          Compare
        </h1>
        <Link className="mono" href={`/runs/${data.run_a}`}>
          {data.run_a}
        </Link>
        <span aria-hidden="true">→</span>
        <Link className="mono" href={`/runs/${data.run_b}`}>
          {data.run_b}
        </Link>
      </Row>

      <h2 className="section-title">What changed?</h2>
      <table className="table" style={{ maxWidth: 560 }}>
        <tbody>
          {data.dimensions.map((dimension) => (
            <tr key={dimension.dimension} style={{ cursor: "default" }}>
              <td>{label(dimension.dimension)}</td>
              <td>
                {dimension.changed ? (
                  <Tag tone="warn">
                    Changed{dimension.differences > 1 ? ` (${dimension.differences})` : ""}
                  </Tag>
                ) : (
                  <span style={{ color: "var(--text-faint)" }}>Same</span>
                )}
              </td>
            </tr>
          ))}
          <tr style={{ cursor: "default" }}>
            <td>Output</td>
            <td>{data.output_changed ? <Tag tone="warn">Changed</Tag> : <span>Same</span>}</td>
          </tr>
          <tr style={{ cursor: "default" }}>
            <td>Cost</td>
            <td>
              <Delta value={data.cost_delta_percent} invert />
              <span style={{ color: "var(--text-faint)" }}>
                {" "}
                {money(data.summary_a.cost)} → {money(data.summary_b.cost)}
              </span>
            </td>
          </tr>
          <tr style={{ cursor: "default" }}>
            <td>Latency</td>
            <td>
              <Delta value={data.latency_delta_percent} invert />
              <span style={{ color: "var(--text-faint)" }}>
                {" "}
                {duration(data.summary_a.duration_ms)} → {duration(data.summary_b.duration_ms)}
              </span>
            </td>
          </tr>
          <tr style={{ cursor: "default" }}>
            <td>Quality</td>
            <td>
              {data.quality_delta === null ? (
                <span style={{ color: "var(--text-faint)" }}>
                  not evaluated — save these runs as tests to score them
                </span>
              ) : (
                <Delta value={data.quality_delta * 100} />
              )}
            </td>
          </tr>
        </tbody>
      </table>

      <h2 className="section-title">Observed ({data.differences.length})</h2>
      {data.differences.length === 0 ? (
        <p style={{ color: "var(--text-muted)" }}>
          Nothing measurable changed between these two runs.
        </p>
      ) : (
        data.differences.map((difference) => {
          const id = `${difference.dimension}.${difference.field}`;
          return (
            <div className="ctx-item" key={id}>
              <button
                type="button"
                className="ctx-head"
                style={{ width: "100%", textAlign: "left" }}
                aria-expanded={open === id}
                onClick={() => setOpen(open === id ? null : id)}
              >
                <Tag tone="accent">{difference.dimension}</Tag>
                <strong>{difference.field}</strong>
                <span style={{ color: "var(--text-muted)" }}>{difference.kind}</span>
                <span className="spacer" />
                <span style={{ color: "var(--text-faint)" }}>
                  {truncate(String(difference.before ?? "—"), 40)} →{" "}
                  {truncate(String(difference.after ?? "—"), 40)}
                </span>
              </button>
              {open === id ? (
                <div className="grid-2" style={{ marginTop: 8 }}>
                  <pre className="event-payload" tabIndex={0} role="region" aria-label="Before">
                    {json(difference.before) || "(absent)"}
                  </pre>
                  <pre className="event-payload" tabIndex={0} role="region" aria-label="After">
                    {json(difference.after) || "(absent)"}
                  </pre>
                </div>
              ) : null}
            </div>
          );
        })
      )}

      <h2 className="section-title">Why this matters</h2>
      {data.explanations.length === 0 ? (
        <p style={{ color: "var(--text-muted)" }}>
          No upstream change lines up with the outcome, so Rewyn offers no explanation.
        </p>
      ) : (
        data.explanations.map((explanation, index) => (
          <div className="card" key={index} style={{ marginBottom: 8 }}>
            <Row>
              <Tag tone="warn">Inference</Tag>
              <span>{percent(explanation.confidence * 100)} confidence</span>
              <span className="spacer" />
              <span style={{ color: "var(--text-faint)" }}>
                {explanation.cause
                  ? `${explanation.cause} → ${explanation.observed}`
                  : explanation.observed}
              </span>
            </Row>
            <p style={{ margin: "6px 0 0" }}>{explanation.rationale}</p>
          </div>
        ))
      )}
      <Narrative runId={data.run_b} against={data.run_a} />

      <p style={{ color: "var(--text-faint)" }}>
        Observed changes are facts read from the two recordings. Inferences are hypotheses drawn
        from them, and are never stated as certainties.
      </p>
    </div>
  );
}

/**
 * "Explain difference" (UI §23).
 *
 * Asked for, never volunteered: on the cloud, and locally when a model is
 * configured, this costs a model call. The answer says who wrote it, and any
 * sentence the explanation agent produced that cited no evidence is shown as
 * removed rather than quietly dropped.
 */
function Narrative({ runId, against }: { runId: string; against: string }) {
  const [narrative, setNarrative] = useState<NarrativeView | null>(null);
  const [problem, setProblem] = useState<ProblemDetail | null>(null);
  const [busy, setBusy] = useState(false);

  const explain = async () => {
    setBusy(true);
    try {
      setNarrative(
        await post<NarrativeView>(
          `/runs/${runId}/explain?against=${encodeURIComponent(against)}`,
          null,
        ),
      );
      setProblem(null);
    } catch (error: unknown) {
      if (error instanceof ApiError) setProblem(error.problem);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card" aria-labelledby="explain" style={{ marginBottom: 12 }}>
      <Row>
        <h2 className="section-title" id="explain" style={{ margin: 0 }}>
          Explain difference
        </h2>
        <span className="spacer" />
        <button className="btn" type="button" onClick={() => void explain()} disabled={busy}>
          {busy ? "Explaining…" : narrative ? "Explain again" : "Explain difference"}
        </button>
      </Row>

      {problem ? <Problem problem={problem} /> : null}

      {!narrative && !problem ? (
        <p style={{ color: "var(--text-muted)", margin: "8px 0 0" }}>
          Rewyn will write this up in prose, from the differences above and nothing else.
        </p>
      ) : null}

      {narrative ? (
        <>
          <p style={{ margin: "8px 0" }}>{narrative.summary}</p>
          {narrative.claims.map((claim, index) => (
            <div className="narrative-claim" key={index}>
              <Tag tone={claim.label === "Observed" ? "accent" : "warn"}>{claim.label}</Tag>
              <div>
                <div>{claim.text}</div>
                <div className="narrative-evidence">{claim.evidence.join(" · ")}</div>
              </div>
            </div>
          ))}
          {narrative.dropped.length > 0 ? (
            <div style={{ marginTop: 8 }}>
              <div className="metric-label">Removed: cited no evidence</div>
              {narrative.dropped.map((text) => (
                <p key={text} style={{ margin: "2px 0", color: "var(--text-faint)" }}>
                  <s>{text}</s>
                </p>
              ))}
            </div>
          ) : null}
          <Row gap={6}>
            <Tag tone={narrative.author === "model" ? "accent" : "default"}>
              {narrative.author === "model" ? `written by ${narrative.model}` : "composed by rule"}
            </Tag>
            {narrative.run_id ? (
              <Link className="btn btn-ghost" href={`/runs/${narrative.run_id}`}>
                The explanation is itself a run
              </Link>
            ) : null}
          </Row>
          <p style={{ color: "var(--text-faint)", margin: "6px 0 0" }}>{narrative.note}</p>
        </>
      ) : null}
    </section>
  );
}
