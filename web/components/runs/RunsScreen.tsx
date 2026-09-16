"use client";

/**
 * The runs page (UI spec §7).
 *
 * Every filter the spec lists is here, applied server-side and encoded in the
 * URL so a filtered view is a link you can send to someone. The vocabulary in
 * each dropdown comes from the facet endpoint, so it only ever offers values
 * that exist.
 */

import { useMemo, useState } from "react";

import type { RunFacets, RunPage } from "@/api/types";
import { useApi, useDebounced } from "@/hooks/useApi";
import { count } from "@/lib/format";
import { useRouter } from "@/lib/router";
import { Empty, Problem, Skeleton } from "@/components/common/atoms";
import { SavedViews } from "@/components/common/SavedViews";
import { RunsTable } from "./RunsTable";

const FILTERS: { key: string; label: string; facet: keyof RunFacets }[] = [
  { key: "agent", label: "Agent", facet: "agent" },
  { key: "model", label: "Model", facet: "model" },
  { key: "status", label: "Status", facet: "status" },
  { key: "user", label: "User", facet: "user" },
  { key: "environment", label: "Environment", facet: "environment" },
  { key: "tool", label: "Tool", facet: "tool" },
  { key: "mcp", label: "MCP", facet: "mcp" },
  { key: "skill", label: "Skill", facet: "skill" },
];

export function RunsScreen({ environment }: { environment: string | null }) {
  const { params, setParam } = useRouter();
  const [text, setText] = useState(params.get("q") ?? "");
  const q = useDebounced(text);

  const filters = useMemo(() => {
    const active: Record<string, string> = {};
    for (const filter of FILTERS) {
      const value = params.get(filter.key);
      if (value) active[filter.key] = value;
    }
    return active;
    // `params` is replaced on every navigation, so identity is the dependency.
  }, [params]);

  const request = {
    ...filters,
    q: q || undefined,
    environment: filters.environment ?? environment ?? undefined,
    error: params.get("error") === "true" ? true : undefined,
    min_cost: params.get("min_cost") ?? undefined,
    max_latency_ms: params.get("max_latency_ms") ?? undefined,
    min_score: params.get("min_score") ?? undefined,
    limit: 100,
  };

  const { data, problem, loading, reload } = useApi<RunPage>("/runs", request);
  const { data: facets } = useApi<RunFacets>("/runs/facets");
  const applied = Object.keys(filters).length + (q ? 1 : 0);

  return (
    <div className="page">
      <h1 className="page-title">Runs</h1>
      <p className="page-sub">
        {data
          ? `${count(data.total)} runs${applied ? ` matching ${applied} filter${applied === 1 ? "" : "s"}` : ""}`
          : "…"}
      </p>

      <div style={{ marginBottom: 8 }}>
        <SavedViews screen="runs" />
      </div>

      <div
        className="card"
        style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}
      >
        <label className="visually-hidden" htmlFor="runs-search">
          Search runs
        </label>
        <input
          id="runs-search"
          className="input"
          style={{ flex: "1 1 220px" }}
          placeholder="Search id, agent, error, dependency…"
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            setParam("q", event.target.value || null);
          }}
        />
        {FILTERS.map((filter) => {
          const options = facets?.[filter.facet] ?? [];
          return (
            <span key={filter.key}>
              <label className="visually-hidden" htmlFor={`filter-${filter.key}`}>
                {filter.label}
              </label>
              <select
                id={`filter-${filter.key}`}
                className="select"
                value={filters[filter.key] ?? ""}
                onChange={(event) => setParam(filter.key, event.target.value || null)}
              >
                <option value="">{filter.label}</option>
                {options.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.value} ({option.count})
                  </option>
                ))}
              </select>
            </span>
          );
        })}
        <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input
            type="checkbox"
            checked={params.get("error") === "true"}
            onChange={(event) => setParam("error", event.target.checked ? "true" : null)}
          />
          Failures only
        </label>
        {applied ? (
          <button
            className="btn btn-ghost"
            type="button"
            onClick={() => {
              for (const filter of FILTERS) setParam(filter.key, null);
              setParam("error", null);
              setParam("q", null);
              setText("");
            }}
          >
            Clear
          </button>
        ) : null}
      </div>

      <div style={{ marginTop: 14 }}>
        {problem ? <Problem problem={problem} onRetry={reload} /> : null}
        {loading && !data ? <Skeleton rows={12} /> : null}
        {data && data.runs.length === 0 ? (
          <Empty title="No runs match">
            <p>
              {applied
                ? "Every filter is combined with AND. Clear one to widen the search."
                : "Record a run and it appears here within a second."}
            </p>
          </Empty>
        ) : null}
        {data && data.runs.length > 0 ? <RunsTable runs={data.runs} /> : null}
      </div>
    </div>
  );
}
