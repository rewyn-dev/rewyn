"use client";

/**
 * Save as Test (UI spec §24).
 *
 * The dialog the spec prints: dataset, expected outcome, evaluator, severity.
 * The production failure becomes a permanent regression case, and the case
 * remembers which run it came from.
 */

import { useEffect, useRef, useState } from "react";

import { post } from "@/api/client";
import type { DatasetDetail, DatasetSummary, ProblemDetail } from "@/api/types";
import { useApi } from "@/hooks/useApi";
import { json } from "@/lib/format";
import { useRouter } from "@/lib/router";
import { Problem, Row } from "@/components/common/atoms";

const SEVERITIES = ["critical", "high", "medium", "low"] as const;

const EVALUATORS = [
  "task-success",
  "correctness",
  "groundedness",
  "safety",
  "tool-selection",
  "structured-output",
];

export function SaveAsTest({
  runId,
  output,
  onClose,
}: {
  runId: string;
  output: unknown;
  onClose: () => void;
}) {
  const { navigate } = useRouter();
  const { data: datasets } = useApi<DatasetSummary[]>("/datasets");
  const [dataset, setDataset] = useState("");
  const [expected, setExpected] = useState(() => json(output, 0));
  const [evaluator, setEvaluator] = useState("task-success");
  const [severity, setSeverity] = useState<(typeof SEVERITIES)[number]>("critical");
  const [problem, setProblem] = useState<ProblemDetail | null>(null);
  const [saving, setSaving] = useState(false);
  const first = useRef<HTMLInputElement>(null);

  useEffect(() => first.current?.focus(), []);

  const save = async () => {
    if (!dataset.trim()) return;
    setSaving(true);
    setProblem(null);
    try {
      const saved = await post<DatasetDetail>(`/runs/${runId}/save-as-test`, {
        dataset: dataset.trim(),
        expected: expected.trim() ? expected : null,
        evaluator,
        severity,
        tags: [],
      });
      onClose();
      navigate(`/datasets/${saved.name}`);
    } catch (error) {
      const detail = (error as { problem?: ProblemDetail }).problem;
      setProblem(
        detail ?? { error: "Could not save", detail: String(error), action: null, href: null },
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="overlay"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Save as test"
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
        }}
      >
        <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--border)" }}>
          <strong>Save as test</strong>
          <p style={{ color: "var(--text-muted)", margin: "4px 0 0" }}>
            This run becomes a permanent case in a regression dataset.
          </p>
        </div>
        <div style={{ padding: 16, display: "grid", gap: 12 }}>
          <div>
            <label htmlFor="dataset-name">Dataset</label>
            <input
              ref={first}
              id="dataset-name"
              className="input"
              style={{ width: "100%" }}
              list="dataset-names"
              placeholder="refund-regression"
              value={dataset}
              onChange={(event) => setDataset(event.target.value)}
            />
            <datalist id="dataset-names">
              {(datasets ?? []).map((candidate) => (
                <option key={candidate.name} value={candidate.name} />
              ))}
            </datalist>
          </div>
          <div>
            <label htmlFor="expected">Expected outcome</label>
            <textarea
              id="expected"
              className="input"
              style={{ width: "100%", height: 90, padding: 8 }}
              value={expected}
              onChange={(event) => setExpected(event.target.value)}
            />
            <span style={{ color: "var(--text-faint)" }}>
              Defaults to what this run produced, which makes it a golden example.
            </span>
          </div>
          <Row>
            <div style={{ flex: 1 }}>
              <label htmlFor="evaluator">Evaluator</label>
              <select
                id="evaluator"
                className="select"
                style={{ width: "100%" }}
                value={evaluator}
                onChange={(event) => setEvaluator(event.target.value)}
              >
                {EVALUATORS.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </div>
            <div style={{ flex: 1 }}>
              <label htmlFor="severity">Severity</label>
              <select
                id="severity"
                className="select"
                style={{ width: "100%" }}
                value={severity}
                onChange={(event) => setSeverity(event.target.value as (typeof SEVERITIES)[number])}
              >
                {SEVERITIES.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </div>
          </Row>
          {problem ? <Problem problem={problem} /> : null}
          <Row>
            <button
              className="btn btn-primary"
              type="button"
              onClick={save}
              disabled={saving || !dataset.trim()}
            >
              {saving ? "Saving…" : "Save"}
            </button>
            <button className="btn btn-ghost" type="button" onClick={onClose}>
              Cancel
            </button>
          </Row>
        </div>
      </div>
    </div>
  );
}
