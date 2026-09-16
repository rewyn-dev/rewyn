"use client";

/** The runs table (UI spec §7): STATUS | TIME | AGENT | USER | MODEL | LATENCY | COST. */

import type { RunSummary } from "@/api/types";
import { clock, duration, money, shortId } from "@/lib/format";
import { useRouter } from "@/lib/router";
import { StatusDot, Tag } from "@/components/common/atoms";

export function RunsTable({
  runs,
  selected,
  onSelect,
}: {
  runs: RunSummary[];
  selected?: string;
  onSelect?: (run: RunSummary) => void;
}) {
  const { navigate } = useRouter();
  const open = (run: RunSummary) => (onSelect ? onSelect(run) : navigate(`/runs/${run.id}`));

  return (
    <table className="table">
      <thead>
        <tr>
          <th scope="col" style={{ width: 28 }}>
            <span className="visually-hidden">Status</span>
          </th>
          <th scope="col" style={{ width: 84 }}>
            Time
          </th>
          <th scope="col">Agent</th>
          <th scope="col" style={{ width: 130 }}>
            User
          </th>
          <th scope="col" style={{ width: 190 }}>
            Model
          </th>
          <th scope="col" className="num" style={{ width: 84 }}>
            Latency
          </th>
          <th scope="col" className="num" style={{ width: 84 }}>
            Cost
          </th>
          <th scope="col" style={{ width: 120 }}>
            Run
          </th>
        </tr>
      </thead>
      <tbody>
        {runs.map((run) => (
          <tr
            key={run.id}
            data-selected={run.id === selected}
            tabIndex={0}
            onClick={() => open(run)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                open(run);
              }
            }}
          >
            <td>
              <StatusDot status={run.status} label={run.status} />
            </td>
            <td className="mono">{clock(run.started_at)}</td>
            <td title={run.error ?? undefined}>
              {run.agent ?? run.name}
              {run.tags.includes("replay") ? (
                <>
                  {" "}
                  <Tag tone="accent" title="A replay of an earlier run, recorded like any other">
                    replay
                  </Tag>
                </>
              ) : null}
              {run.error ? (
                <span style={{ color: "var(--text-faint)" }}> — {run.error.split("\n")[0]}</span>
              ) : null}
            </td>
            <td>{run.user ?? "—"}</td>
            <td className="mono">{run.model ?? "—"}</td>
            <td className="num">{duration(run.duration_ms)}</td>
            <td className="num">{money(run.cost)}</td>
            <td className="mono" style={{ color: "var(--text-faint)" }}>
              {shortId(run.id)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
