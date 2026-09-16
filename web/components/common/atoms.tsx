"use client";

/** The small pieces every screen reuses (UI spec §3). */

import type { ReactNode } from "react";

import { statusTone } from "@/lib/format";
import { Link } from "@/lib/router";

export function StatusDot({ status, label }: { status: string; label?: string }) {
  const tone = statusTone(status);
  return (
    <span
      className={`dot dot-${tone}`}
      role="img"
      aria-label={label ?? status}
      title={label ?? status}
    />
  );
}

export function Tag({
  children,
  tone = "default",
  title,
}: {
  children: ReactNode;
  tone?: "default" | "ok" | "fail" | "warn" | "accent";
  title?: string;
}) {
  return (
    <span className={tone === "default" ? "tag" : `tag tag-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function Metric({
  label,
  value,
  hint,
  href,
}: {
  label: string;
  value: string;
  hint?: string;
  /** Where this number is explained. UI §4 has no slot for Cost, so the
   *  number that raises the question is the way in. */
  href?: string;
}) {
  const body = (
    <>
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      {hint ? (
        <div style={{ color: "var(--text-faint)", fontSize: "var(--size-sm)" }}>{hint}</div>
      ) : null}
    </>
  );
  if (!href) return <div className="card">{body}</div>;
  return (
    <Link className="card" href={href} style={{ display: "block" }}>
      {body}
    </Link>
  );
}

export function Skeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-live="polite">
      <span className="visually-hidden">Loading</span>
      {Array.from({ length: rows }, (_, index) => (
        <div className="skeleton" key={index} />
      ))}
    </div>
  );
}

export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <h2>{title}</h2>
      <div>{children}</div>
      {action ? <div style={{ marginTop: 14 }}>{action}</div> : null}
    </div>
  );
}

export function Problem({
  problem,
  onRetry,
}: {
  problem: { error: string; detail: string; action: string | null; href: string | null };
  onRetry?: () => void;
}) {
  return (
    <div className="problem" role="alert">
      <h3>{problem.error}</h3>
      <p>{problem.detail}</p>
      <div style={{ display: "flex", gap: 8 }}>
        {problem.action && problem.href ? (
          <a className="btn" href={problem.href}>
            {problem.action}
          </a>
        ) : null}
        {onRetry ? (
          <button className="btn" type="button" onClick={onRetry}>
            Try again
          </button>
        ) : null}
      </div>
    </div>
  );
}

/**
 * A scrollable block of recorded text: a payload, a prompt, a tool response.
 *
 * It is focusable on purpose -- a region that scrolls must be reachable from
 * the keyboard, or its content is unreadable without a mouse (UI §49).
 */
export function Code({
  children,
  label,
  maxHeight = 200,
}: {
  children: ReactNode;
  label: string;
  maxHeight?: number;
}) {
  return (
    <pre
      className="event-payload"
      style={{ padding: 8, margin: "6px 0 0", maxHeight }}
      tabIndex={0}
      role="region"
      aria-label={label}
    >
      {children}
    </pre>
  );
}

export function Mono({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span className="mono" title={title}>
      {children}
    </span>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span
        style={{
          fontSize: "var(--size-xs)",
          color: "var(--text-faint)",
          textTransform: "uppercase",
          letterSpacing: "0.1em",
        }}
      >
        {label}
      </span>
      <span>{children}</span>
    </div>
  );
}

export function Row({ children, gap = 10 }: { children: ReactNode; gap?: number }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap, flexWrap: "wrap" }}>{children}</div>
  );
}
