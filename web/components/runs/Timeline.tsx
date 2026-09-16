"use client";

/**
 * The run timeline (UI spec §9).
 *
 * One row per event, ordered, nested by span, every row expandable to its
 * payload. Long runs arrive a page at a time (UI §50), and a row can be
 * linked to directly so a finding can be shared.
 */

import { useEffect, useState } from "react";

import { get } from "@/api/client";
import type { TimelineView } from "@/api/types";
import { useApi } from "@/hooks/useApi";
import { duration, json, offset } from "@/lib/format";
import { Code, Problem, Skeleton, StatusDot } from "@/components/common/atoms";

export function Timeline({ runId }: { runId: string }) {
  const { data, problem, loading, reload } = useApi<TimelineView>(`/runs/${runId}/timeline`, {
    limit: 200,
  });
  const [entries, setEntries] = useState<TimelineView["entries"]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    if (!data) return;
    setEntries(data.entries);
    setNext(data.next_seq);
    const hash = Number(window.location.hash.replace("#event-", ""));
    setOpen(Number.isFinite(hash) && hash > 0 ? hash : null);
  }, [data]);

  if (problem) return <Problem problem={problem} onRetry={reload} />;
  if (loading && entries.length === 0) return <Skeleton rows={10} />;

  const more = async () => {
    if (next === null) return;
    setLoadingMore(true);
    try {
      const page = await get<TimelineView>(`/runs/${runId}/timeline`, {
        after_seq: next,
        limit: 200,
      });
      setEntries((current) => [...current, ...page.entries]);
      setNext(page.next_seq);
    } finally {
      setLoadingMore(false);
    }
  };

  return (
    <div className="timeline">
      {entries.map((entry) => {
        const expanded = open === entry.seq;
        return (
          <div key={entry.seq} id={`event-${entry.seq}`}>
            <button
              type="button"
              className="event"
              aria-expanded={expanded}
              onClick={() => setOpen(expanded ? null : entry.seq)}
            >
              <span className="event-time">{offset(entry.offset_ms)}</span>
              <StatusDot status={entry.status} label={entry.status} />
              <span style={{ paddingLeft: entry.depth * 12, minWidth: 0 }}>
                <span className="event-label">{entry.label}</span>{" "}
                <span className="event-detail">{entry.detail}</span>
              </span>
              <span className="mono" style={{ color: "var(--text-faint)" }}>
                {entry.duration_ms !== null ? duration(entry.duration_ms) : ""}
              </span>
            </button>
            {expanded ? (
              <Code label={`${entry.label} payload`} maxHeight={340}>
                {json(entry.payload) || "(no payload)"}
              </Code>
            ) : null}
          </div>
        );
      })}
      {next !== null ? (
        <button
          className="btn"
          type="button"
          onClick={more}
          disabled={loadingMore}
          style={{ margin: 12 }}
        >
          {loadingMore ? "Loading…" : `Load more (${data ? data.total - entries.length : 0} left)`}
        </button>
      ) : null}
    </div>
  );
}
