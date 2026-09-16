"use client";

/**
 * The context inspector (UI spec §11) and its composition (UI §12).
 *
 * This is the differentiator: not "the prompt was 16k tokens" but which item
 * came from where, at which version, costing how much of the budget, and what
 * was dropped to make room. Items the caller may not read are shown as
 * withheld rather than omitted (UI §54) -- the shape of the context stays
 * honest even when its content is not visible.
 */

import { useState } from "react";

import type { ContextAssembly, ContextItemView, ContextView } from "@/api/types";
import { useApi } from "@/hooks/useApi";
import { ago, kindColour, tokens } from "@/lib/format";
import { Empty, Problem, Row, Skeleton, Tag } from "@/components/common/atoms";

function BudgetBar({ assembly }: { assembly: ContextAssembly }) {
  const entries = Object.entries(assembly.by_kind).sort((a, b) => b[1] - a[1]);
  const budget = Math.max(assembly.budget, assembly.used, 1);
  const free = Math.max(budget - assembly.used, 0);
  return (
    <div>
      <Row>
        <strong>{tokens(assembly.used)}</strong>
        <span style={{ color: "var(--text-muted)" }}>of {tokens(assembly.budget)} tokens used</span>
        <span className="spacer" />
        <span style={{ color: "var(--text-faint)" }}>
          assembled in {assembly.assembled_in_ms.toFixed(1)}ms
        </span>
      </Row>
      <div
        className="budget"
        style={{ marginTop: 8 }}
        role="img"
        aria-label={entries.map(([kind, value]) => `${kind} ${value} tokens`).join(", ")}
      >
        {entries.map(([kind, value]) => (
          <span
            key={kind}
            style={{ width: `${(value / budget) * 100}%`, background: kindColour(kind) }}
            title={`${kind}: ${tokens(value)}`}
          />
        ))}
        {free > 0 ? <span style={{ width: `${(free / budget) * 100}%` }} /> : null}
      </div>
      <div className="budget-legend">
        {entries.map(([kind, value]) => (
          <span key={kind}>
            <span className="legend-swatch" style={{ background: kindColour(kind) }} />
            {kind}
            <span style={{ color: "var(--text-faint)" }}> {tokens(value)}</span>
          </span>
        ))}
        {free > 0 ? (
          <span style={{ color: "var(--text-faint)" }}>
            <span className="legend-swatch" style={{ background: "var(--bg-inset)" }} />
            free {tokens(free)}
          </span>
        ) : null}
      </div>
    </div>
  );
}

function Item({ item }: { item: ContextItemView }) {
  return (
    <div className="ctx-item" data-excluded={!item.included}>
      <div className="ctx-head">
        <span className="legend-swatch" style={{ background: kindColour(item.kind) }} />
        <strong>{item.redacted ? "Withheld" : (item.title ?? item.id)}</strong>
        <Tag>{item.kind}</Tag>
        {item.version ? <Tag tone="accent">v{item.version}</Tag> : null}
        {item.trust_level === "untrusted" ? <Tag tone="warn">untrusted</Tag> : null}
        {item.verified === false ? <Tag tone="fail">hash mismatch</Tag> : null}
        {!item.included ? <Tag tone="warn">excluded: {item.excluded_reason}</Tag> : null}
        <span className="spacer" />
        {item.uri && !item.redacted ? (
          <a className="btn btn-ghost" href={item.uri} target="_blank" rel="noreferrer">
            View source
          </a>
        ) : null}
      </div>
      {item.redacted ? (
        <p style={{ color: "var(--text-muted)", margin: "6px 0 0" }}>{item.redaction_reason}</p>
      ) : null}
      <div className="ctx-meta">
        <span>
          Source <b>{item.source ?? "inline"}</b>
        </span>
        {item.record ? (
          <span>
            Record <b>{item.record}</b>
          </span>
        ) : null}
        <span>
          Tokens <b>{tokens(item.tokens)}</b>
        </span>
        <span>
          Relevance <b>{item.relevance.toFixed(2)}</b>
        </span>
        <span>
          Authority <b>{item.authority.toFixed(2)}</b>
        </span>
        {item.retrieved_at ? (
          <span>
            Updated <b>{ago(item.retrieved_at)}</b>
          </span>
        ) : null}
        {item.hash ? (
          <span className="mono" title={item.hash}>
            {item.hash.slice(0, 22)}
          </span>
        ) : null}
      </div>
    </div>
  );
}

export function ContextInspector({ runId }: { runId: string }) {
  const { data, problem, loading, reload } = useApi<ContextView>(`/runs/${runId}/context`);
  const [index, setIndex] = useState(0);

  if (problem) return <Problem problem={problem} onRetry={reload} />;
  if (loading && !data) return <Skeleton rows={8} />;
  if (!data || data.assemblies.length === 0) {
    return (
      <Empty title="No context was assembled">
        <p>
          This run called the model directly. Wire a <code>Context</code> into the agent and every
          item it selects, and everything it dropped, shows up here.
        </p>
      </Empty>
    );
  }

  const assembly = data.assemblies[Math.min(index, data.assemblies.length - 1)]!;
  const included = assembly.items.filter((item) => item.included);
  const excluded = assembly.items.filter((item) => !item.included);

  return (
    <div>
      {data.assemblies.length > 1 ? (
        <Row>
          <span style={{ color: "var(--text-muted)" }}>Assembly</span>
          {data.assemblies.map((candidate, position) => (
            <button
              key={candidate.seq}
              type="button"
              className={position === index ? "btn btn-primary" : "btn btn-ghost"}
              onClick={() => setIndex(position)}
            >
              #{position + 1}
            </button>
          ))}
        </Row>
      ) : null}

      <div className="card" style={{ margin: "10px 0 16px" }}>
        <BudgetBar assembly={assembly} />
      </div>

      {assembly.query ? (
        <p style={{ color: "var(--text-muted)" }}>
          Query: <span className="mono">{assembly.query}</span>
        </p>
      ) : null}
      {assembly.dropped_untrusted.length > 0 ? (
        <p>
          <Tag tone="warn">
            {assembly.dropped_untrusted.length} untrusted item(s) dropped before assembly
          </Tag>
        </p>
      ) : null}

      <h3 className="section-title">Included ({included.length})</h3>
      {included.map((item) => (
        <Item key={item.id} item={item} />
      ))}

      {excluded.length > 0 ? (
        <>
          <h3 className="section-title">Excluded ({excluded.length})</h3>
          {excluded.map((item) => (
            <Item key={item.id} item={item} />
          ))}
        </>
      ) : null}
    </div>
  );
}
