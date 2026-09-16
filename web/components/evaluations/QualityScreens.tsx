"use client";

/**
 * Evaluations and regression (UI spec §26, §27).
 *
 * Evaluations answer "is it good?" with distributions rather than a single
 * number, because a mean hides the failures. Regression answers "did my
 * change make it worse?" with a baseline beside the candidate and, most
 * importantly, the list of cases that became worse -- each one a click from
 * the comparison that explains it.
 */

import { useState } from "react";

import { post } from "@/api/client";
import type {
  EvaluationsView,
  ExperimentPlan,
  RegressionDetail,
  RegressionSummary,
  ScoreBucket,
} from "@/api/types";
import { useApi } from "@/hooks/useApi";
import { ago, count, duration, money, percent, truncate } from "@/lib/format";
import { Link } from "@/lib/router";
import { Empty, Metric, Problem, Row, Skeleton, Tag } from "@/components/common/atoms";

function Histogram({ buckets }: { buckets: ScoreBucket[] }) {
  const peak = Math.max(...buckets.map((bucket) => bucket.count), 1);
  return (
    <div
      style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 56 }}
      role="img"
      aria-label={buckets
        .filter((bucket) => bucket.count > 0)
        .map((bucket) => `${bucket.lower}-${bucket.upper}: ${bucket.count}`)
        .join(", ")}
    >
      {buckets.map((bucket) => (
        <div
          key={bucket.lower}
          title={`${bucket.lower.toFixed(1)}–${bucket.upper.toFixed(1)}: ${bucket.count}`}
          style={{
            flex: 1,
            height: `${Math.max((bucket.count / peak) * 100, bucket.count > 0 ? 6 : 2)}%`,
            background: bucket.count > 0 ? "var(--accent)" : "var(--bg-inset)",
            borderRadius: 2,
          }}
        />
      ))}
    </div>
  );
}

export function EvaluationsScreen() {
  const { data, problem, loading, reload } = useApi<EvaluationsView>("/evaluations");
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
    <div className="page page-narrow">
      <h1 className="page-title">Evaluations</h1>
      <p className="page-sub">Is it good? One distribution per evaluator, not one number.</p>

      {data.evaluators.length === 0 ? (
        <Empty title="Nothing has been evaluated yet">
          <p>Score a run with an evaluator and its distribution appears here:</p>
          <pre>{`from rewyn.evaluation.evaluator import evaluator

@evaluator
def task_success(subject):
    return "refund issued" in subject.output`}</pre>
          <Link className="btn" href="/datasets">
            Datasets
          </Link>
        </Empty>
      ) : (
        <>
          <div className="metrics">
            <Metric label="Evaluators" value={count(data.evaluators.length)} />
            <Metric label="Scores" value={count(data.scores)} />
            <Metric label="Runs scored" value={count(data.runs_scored)} />
          </div>
          <div className="grid-2" style={{ marginTop: 14 }}>
            {data.evaluators.map((evaluatorSummary) => (
              <div className="card" key={evaluatorSummary.name}>
                <Row>
                  <strong>{evaluatorSummary.name}</strong>
                  <Tag tone={evaluatorSummary.pass_rate >= 90 ? "ok" : "warn"}>
                    {percent(evaluatorSummary.pass_rate)} pass
                  </Tag>
                  <span className="spacer" />
                  <span style={{ color: "var(--text-faint)" }}>
                    {count(evaluatorSummary.scores)} scores · {ago(evaluatorSummary.last_scored_at)}
                  </span>
                </Row>
                <div style={{ marginTop: 10 }}>
                  <Histogram buckets={evaluatorSummary.distribution} />
                  <Row>
                    <span style={{ color: "var(--text-faint)" }}>0.0</span>
                    <span className="spacer" />
                    <span>mean {evaluatorSummary.mean.toFixed(2)}</span>
                    <span className="spacer" />
                    <span style={{ color: "var(--text-faint)" }}>1.0</span>
                  </Row>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function NewExperiment() {
  const [form, setForm] = useState({
    dataset: "",
    target: "",
    baseline: "latest",
    evaluators: "",
    min_success: "",
  });
  const [plan, setPlan] = useState<ExperimentPlan | null>(null);
  const [copied, setCopied] = useState(false);

  const build = async () => {
    const built = await post<ExperimentPlan>("/experiments:plan", {
      dataset: form.dataset,
      target: form.target,
      baseline: form.baseline || null,
      evaluators: form.evaluators
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean),
      min_success: form.min_success ? Number(form.min_success) : null,
      max_cost: null,
      concurrency: 1,
    });
    setPlan(built);
    setCopied(false);
  };

  return (
    <div className="card">
      <div className="metric-label">New experiment</div>
      <div style={{ display: "grid", gap: 10, marginTop: 8, maxWidth: 560 }}>
        <div>
          <label htmlFor="exp-dataset">Dataset</label>
          <input
            id="exp-dataset"
            className="input"
            style={{ width: "100%" }}
            placeholder="refund-regression"
            value={form.dataset}
            onChange={(event) => setForm({ ...form, dataset: event.target.value })}
          />
        </div>
        <div>
          <label htmlFor="exp-target">Candidate</label>
          <input
            id="exp-target"
            className="input"
            style={{ width: "100%" }}
            placeholder="app.agents:refund_agent"
            value={form.target}
            onChange={(event) => setForm({ ...form, target: event.target.value })}
          />
        </div>
        <Row>
          <div style={{ flex: 1 }}>
            <label htmlFor="exp-baseline">Baseline</label>
            <input
              id="exp-baseline"
              className="input"
              style={{ width: "100%" }}
              placeholder="latest"
              value={form.baseline}
              onChange={(event) => setForm({ ...form, baseline: event.target.value })}
            />
          </div>
          <div style={{ flex: 1 }}>
            <label htmlFor="exp-min">Minimum success</label>
            <input
              id="exp-min"
              className="input"
              style={{ width: "100%" }}
              placeholder="0.95"
              value={form.min_success}
              onChange={(event) => setForm({ ...form, min_success: event.target.value })}
            />
          </div>
        </Row>
        <div>
          <label htmlFor="exp-evaluators">Evaluators</label>
          <input
            id="exp-evaluators"
            className="input"
            style={{ width: "100%" }}
            placeholder="app.evals:task_success, app.evals:groundedness"
            value={form.evaluators}
            onChange={(event) => setForm({ ...form, evaluators: event.target.value })}
          />
        </div>
        <Row>
          <button
            className="btn btn-primary"
            type="button"
            onClick={build}
            disabled={!form.dataset}
          >
            Run
          </button>
        </Row>
      </div>

      {plan ? (
        <div style={{ marginTop: 12 }}>
          <p style={{ color: "var(--text-muted)" }}>{plan.explanation}</p>
          <Row>
            <code
              className="event-payload"
              style={{ flex: 1, padding: 8, margin: 0 }}
              tabIndex={0}
              role="region"
              aria-label="Command to run this experiment"
            >
              {plan.command}
            </code>
            <button
              className="btn"
              type="button"
              onClick={() => {
                void navigator.clipboard?.writeText(plan.command);
                setCopied(true);
              }}
            >
              {copied ? "Copied" : "Copy"}
            </button>
          </Row>
        </div>
      ) : null}
    </div>
  );
}

export function RegressionScreen() {
  const { data, problem, loading, reload } = useApi<RegressionSummary[]>("/regression");
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
    <div className="page">
      <h1 className="page-title">Regression</h1>
      <p className="page-sub">Did my change make it worse?</p>

      <NewExperiment />

      <h2 className="section-title">Runs</h2>
      {(data ?? []).length === 0 ? (
        <Empty title="No regression runs yet">
          <p>
            Build a dataset from your runs, then run it against your agent. The report appears here,
            with the cases that became worse.
          </p>
          <Link className="btn" href="/datasets">
            Datasets
          </Link>
        </Empty>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th scope="col" style={{ width: 28 }} />
              <th scope="col">Report</th>
              <th scope="col">Dataset</th>
              <th scope="col">Target</th>
              <th scope="col" className="num">
                Tests
              </th>
              <th scope="col" className="num">
                Success
              </th>
              <th scope="col" className="num">
                Δ
              </th>
              <th scope="col" className="num">
                Avg cost
              </th>
              <th scope="col">When</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((report) => (
              <tr key={report.id}>
                <td>
                  <span
                    className={report.passed ? "dot dot-ok" : "dot dot-fail"}
                    role="img"
                    aria-label={report.passed ? "passed" : "failed"}
                  />
                </td>
                <td className="mono">
                  <Link href={`/regression/${report.id}`}>{report.id.slice(0, 14)}</Link>
                </td>
                <td>{report.dataset}</td>
                <td className="mono">{report.target || "—"}</td>
                <td className="num">{count(report.tests)}</td>
                <td className="num">{percent(report.success_rate)}</td>
                <td className="num">
                  {report.success_delta === null ? (
                    "—"
                  ) : (
                    <span
                      style={{
                        color: report.success_delta >= 0 ? "var(--ok)" : "var(--fail)",
                      }}
                    >
                      {report.success_delta > 0 ? "+" : ""}
                      {report.success_delta.toFixed(1)}%
                    </span>
                  )}
                </td>
                <td className="num">{money(report.avg_cost)}</td>
                <td>{ago(report.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function RegressionReportScreen({ id }: { id: string }) {
  const { data, problem, loading, reload } = useApi<RegressionDetail>(`/regression/${id}`);
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
        <Link className="btn btn-ghost" href="/regression">
          ← Regression
        </Link>
        <h1 className="page-title" style={{ margin: 0 }}>
          {data.dataset}
        </h1>
        <Tag tone={data.passed ? "ok" : "fail"}>{data.passed ? "passed" : "failed"}</Tag>
        <span className="mono" style={{ color: "var(--text-faint)" }}>
          {data.id}
        </span>
      </Row>

      <h2 className="section-title">Baseline vs candidate</h2>
      <table className="table" style={{ maxWidth: 620 }}>
        <thead>
          <tr>
            <th scope="col" />
            <th scope="col" className="num">
              Baseline
            </th>
            <th scope="col" className="num">
              Candidate
            </th>
          </tr>
        </thead>
        <tbody>
          <tr style={{ cursor: "default" }}>
            <td>Task success</td>
            <td className="num">{data.baseline ? percent(data.baseline.success_rate) : "—"}</td>
            <td className="num">{percent(data.success_rate)}</td>
          </tr>
          {data.metrics.map((metric) => (
            <tr key={metric.name} style={{ cursor: "default" }}>
              <td>{metric.name}</td>
              <td className="num">
                {metric.baseline_mean === null ? "—" : metric.baseline_mean.toFixed(2)}
              </td>
              <td className="num">{metric.mean.toFixed(2)}</td>
            </tr>
          ))}
          <tr style={{ cursor: "default" }}>
            <td>Cost</td>
            <td className="num">{data.baseline ? money(data.baseline.avg_cost) : "—"}</td>
            <td className="num">{money(data.avg_cost)}</td>
          </tr>
          <tr style={{ cursor: "default" }}>
            <td>Latency</td>
            <td className="num">{data.baseline ? duration(data.baseline.avg_latency_ms) : "—"}</td>
            <td className="num">{duration(data.avg_latency_ms)}</td>
          </tr>
        </tbody>
      </table>

      {data.gates.length > 0 ? (
        <>
          <h2 className="section-title">Gates</h2>
          <Row gap={6}>
            {data.gates.map((gate) => (
              <Tag key={gate.name} tone={gate.passed ? "ok" : "fail"}>
                {gate.name}: {gate.actual.toFixed(3)} {gate.comparison} {gate.limit}
              </Tag>
            ))}
          </Row>
        </>
      ) : null}

      <h2 className="section-title">
        Regressions ({data.regressions.length} case{data.regressions.length === 1 ? "" : "s"} became
        worse)
      </h2>
      {data.regressions.length === 0 ? (
        <p style={{ color: "var(--text-muted)" }}>
          {data.baseline
            ? "Nothing that passed in the baseline fails now."
            : "No baseline was attached, so nothing can be called a regression."}
        </p>
      ) : (
        <Cases cases={data.regressions} />
      )}

      <h2 className="section-title">All cases ({data.cases.length})</h2>
      <Cases cases={data.cases} />
    </div>
  );
}

function Cases({ cases }: { cases: RegressionDetail["cases"] }) {
  return (
    <table className="table">
      <thead>
        <tr>
          <th scope="col" style={{ width: 28 }} />
          <th scope="col">Case</th>
          <th scope="col">Output</th>
          <th scope="col">Expected</th>
          <th scope="col" className="num">
            Cost
          </th>
          <th scope="col">Run</th>
        </tr>
      </thead>
      <tbody>
        {cases.map((item) => (
          <tr key={item.item_id} style={{ cursor: "default" }}>
            <td>
              <span
                className={item.passed ? "dot dot-ok" : "dot dot-fail"}
                role="img"
                aria-label={item.passed ? "passed" : "failed"}
              />
            </td>
            <td className="mono">
              {item.item_id.slice(0, 14)}
              {item.regressed ? (
                <>
                  {" "}
                  <Tag tone="fail">regressed</Tag>
                </>
              ) : null}
            </td>
            <td title={item.output}>{truncate(item.output || item.error || "—", 60)}</td>
            <td title={String(item.expected ?? "")}>
              {truncate(String(item.expected ?? "—"), 60)}
            </td>
            <td className="num">{money(item.cost)}</td>
            <td>
              {item.run_id ? (
                <Link className="mono" href={`/runs/${item.run_id}`}>
                  open
                </Link>
              ) : (
                "—"
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
