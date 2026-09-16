"use client";

/**
 * Datasets (UI spec §25).
 *
 * A dataset is the memory of what must not break again: every case says which
 * run it came from, what is expected of it, and how bad it is when it fails.
 */

import type { DatasetDetail, DatasetSummary } from "@/api/types";
import { useApi } from "@/hooks/useApi";
import { ago, count, truncate } from "@/lib/format";
import { Link } from "@/lib/router";
import { Empty, Metric, Problem, Row, Skeleton, Tag } from "@/components/common/atoms";

function text(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return typeof value === "string" ? value : JSON.stringify(value);
}

export function DatasetsScreen() {
  const { data, problem, loading, reload } = useApi<DatasetSummary[]>("/datasets");

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
      <h1 className="page-title">Datasets</h1>
      <p className="page-sub">What your agents are tested against.</p>
      {(data ?? []).length === 0 ? (
        <Empty title="No datasets yet">
          <p>Turn a production run into your first regression test.</p>
          <p style={{ color: "var(--text-faint)" }}>
            Open a run, press <kbd>T</kbd>, and pick a dataset name. The run&rsquo;s own output
            becomes the expectation unless you change it.
          </p>
          <Link className="btn" href="/runs">
            Create from a run
          </Link>
        </Empty>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Dataset</th>
              <th scope="col">Version</th>
              <th scope="col" className="num">
                Cases
              </th>
              <th scope="col">Updated</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((dataset) => (
              <tr key={dataset.name}>
                <td>
                  <Link href={`/datasets/${dataset.name}`}>{dataset.name}</Link>
                </td>
                <td className="mono">v{dataset.version}</td>
                <td className="num">{count(dataset.cases)}</td>
                <td>{ago(dataset.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function DatasetScreen({ name }: { name: string }) {
  const { data, problem, loading, reload } = useApi<DatasetDetail>(`/datasets/${name}`);

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
  if (!data) return null;

  return (
    <div className="page">
      <Row>
        <Link className="btn btn-ghost" href="/datasets">
          ← Datasets
        </Link>
        <h1 className="page-title" style={{ margin: 0 }}>
          {data.name}
        </h1>
        <Tag tone="accent">v{data.version}</Tag>
      </Row>

      <div className="metrics" style={{ marginTop: 12 }}>
        <Metric label="Cases" value={count(data.cases)} />
        <Metric
          label="Pass"
          value={data.passed === null ? "—" : count(data.passed)}
          hint={data.passed === null ? "run a regression to fill this in" : undefined}
        />
        <Metric
          label="Fail"
          value={data.failed === null ? "—" : count(data.failed)}
          hint={data.failed === null ? "arrives with the regression screen" : undefined}
        />
        <Metric label="Updated" value={ago(data.updated_at)} />
      </div>

      <h2 className="section-title">Cases</h2>
      <table className="table">
        <thead>
          <tr>
            <th scope="col" style={{ width: 150 }}>
              Case
            </th>
            <th scope="col">Input</th>
            <th scope="col">Expected</th>
            <th scope="col" style={{ width: 120 }}>
              Evaluator
            </th>
            <th scope="col" style={{ width: 90 }}>
              Severity
            </th>
            <th scope="col" style={{ width: 110 }}>
              Last result
            </th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((item) => (
            <tr key={item.id} style={{ cursor: "default" }}>
              <td className="mono">
                {item.source_run_id ? (
                  <Link href={`/runs/${item.source_run_id}`}>from run</Link>
                ) : (
                  item.id.slice(0, 12)
                )}
              </td>
              <td title={text(item.input)}>{truncate(text(item.input), 70)}</td>
              <td title={text(item.expected)}>{truncate(text(item.expected), 70)}</td>
              <td>{item.evaluator ?? "—"}</td>
              <td>
                {item.severity ? (
                  <Tag tone={item.severity === "critical" ? "fail" : "warn"}>{item.severity}</Tag>
                ) : (
                  "—"
                )}
              </td>
              <td style={{ color: "var(--text-faint)" }}>{item.last_result ?? "not run yet"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
