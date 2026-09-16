"use client";

/**
 * The BUILD pages (UI spec §4): models, prompts, skills, tools, MCP, context,
 * memory and graphs.
 *
 * All eight are the same question -- what exists, at which versions, used by
 * how many runs -- so they are one screen parameterised by kind rather than
 * eight near-identical ones.
 */

import type { RegistryDetail, RegistryEntry } from "@/api/types";
import { useApi } from "@/hooks/useApi";
import { ago, count } from "@/lib/format";
import { Link } from "@/lib/router";
import { Empty, Metric, Problem, Row, Skeleton, Tag } from "@/components/common/atoms";
import { RunsTable } from "@/components/runs/RunsTable";

export const REGISTRY_PAGES: Record<string, { title: string; question: string; empty: string }> = {
  models: {
    title: "Models",
    question: "Which models am I running on?",
    empty: "A model appears here the first time a run calls it.",
  },
  prompts: {
    title: "Prompts",
    question: "What am I asking the model?",
    empty:
      "Prompts appear here when they are registered as versioned dependencies. " +
      "A run's exact prompt is always on its Prompt tab.",
  },
  skills: {
    title: "Skills",
    question: "Which skills exist, at which version?",
    empty: "A skill appears here the first time a run loads it.",
  },
  tools: {
    title: "Tools",
    question: "What can my agents actually do?",
    empty: "A tool appears here the first time a run calls it.",
  },
  mcp: {
    title: "MCP",
    question: "Which servers am I depending on?",
    empty: "An MCP server appears here the first time a run connects to it.",
  },
  context: {
    title: "Context",
    question: "How is context assembled?",
    empty: "A context configuration appears here the first time a run assembles one.",
  },
  memory: {
    title: "Memory",
    question: "What does the system remember?",
    empty: "A memory store appears here the first time a run reads or writes it.",
  },
  graphs: {
    title: "Graphs",
    question: "What is the control flow?",
    empty: "A graph appears here the first time it runs.",
  },
};

export function RegistryScreen({ page }: { page: string }) {
  const meta = REGISTRY_PAGES[page]!;
  const { data, problem, loading, reload } = useApi<RegistryEntry[]>(`/registry/${page}`);

  if (problem)
    return (
      <div className="page">
        <Problem problem={problem} onRetry={reload} />
      </div>
    );
  if (loading && !data)
    return (
      <div className="page">
        <Skeleton rows={6} />
      </div>
    );

  return (
    <div className="page page-narrow">
      <h1 className="page-title">{meta.title}</h1>
      <p className="page-sub">{meta.question}</p>
      {(data ?? []).length === 0 ? (
        <Empty title={`No ${meta.title.toLowerCase()} yet`}>
          <p>{meta.empty}</p>
          <Link className="btn" href="/runs">
            Browse runs
          </Link>
        </Empty>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Version</th>
              <th scope="col" className="num">
                Versions
              </th>
              <th scope="col" className="num">
                Runs
              </th>
              <th scope="col">Last used</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((entry) => (
              <tr key={entry.name}>
                <td>
                  <Link href={`/${page}/${encodeURIComponent(entry.name)}`}>{entry.name}</Link>
                </td>
                <td className="mono">{entry.version}</td>
                <td className="num">{count(entry.versions)}</td>
                <td className="num">{count(entry.runs)}</td>
                <td>{ago(entry.last_used_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function RegistryDetailScreen({ page, name }: { page: string; name: string }) {
  const meta = REGISTRY_PAGES[page]!;
  const { data, problem, loading, reload } = useApi<RegistryDetail>(
    `/registry/${page}/${encodeURIComponent(name)}`,
  );

  if (problem)
    return (
      <div className="page">
        <Problem problem={problem} onRetry={reload} />
      </div>
    );
  if (loading && !data)
    return (
      <div className="page">
        <Skeleton rows={6} />
      </div>
    );
  if (!data) return null;

  return (
    <div className="page">
      <Row>
        <Link className="btn btn-ghost" href={`/${page}`}>
          ← {meta.title}
        </Link>
        <h1 className="page-title mono" style={{ margin: 0 }}>
          {data.name}
        </h1>
      </Row>

      <div className="metrics" style={{ marginTop: 12 }}>
        <Metric label="Runs" value={count(data.runs)} />
        <Metric label="Versions" value={count(data.versions.length)} />
        <Metric label="Last used" value={ago(data.versions[0]?.last_seen ?? null)} />
      </div>

      <h2 className="section-title">Used by</h2>
      {data.used_by.length === 0 ? (
        <p style={{ color: "var(--text-muted)" }}>No agent claimed these runs.</p>
      ) : (
        <Row gap={6}>
          {data.used_by.map((agent) => (
            <Link key={agent} href={`/agents/${agent}`}>
              <Tag tone="accent">{agent}</Tag>
            </Link>
          ))}
        </Row>
      )}

      <h2 className="section-title">Versions</h2>
      <table className="table" style={{ maxWidth: 620 }}>
        <thead>
          <tr>
            <th scope="col">Version</th>
            <th scope="col" className="num">
              Runs
            </th>
            <th scope="col">First seen</th>
            <th scope="col">Last seen</th>
          </tr>
        </thead>
        <tbody>
          {data.versions.map((version) => (
            <tr key={version.version} style={{ cursor: "default" }}>
              <td className="mono">{version.version}</td>
              <td className="num">{count(version.runs)}</td>
              <td>{ago(version.first_seen)}</td>
              <td>{ago(version.last_seen)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2 className="section-title">Recent runs</h2>
      <RunsTable runs={data.recent_runs} />
    </div>
  );
}
