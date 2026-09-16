import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DiffView } from "@/api/types";
import { CompareWorkspace } from "@/components/replay/CompareWorkspace";
import { clearCache } from "@/hooks/useApi";
import { RouterProvider } from "@/lib/router";

function summary(id: string, cost: number, duration: number) {
  return {
    id,
    name: "refund",
    status: "succeeded",
    project: "default",
    environment: "production",
    agent: "refund-agent",
    user: "sam",
    model: "gpt-x",
    session_id: null,
    parent_run_id: null,
    started_at: "2026-09-13T10:41:00Z",
    ended_at: "2026-09-13T10:41:04Z",
    duration_ms: duration,
    cost,
    input_tokens: 10,
    output_tokens: 10,
    model_calls: 1,
    tool_calls: 0,
    event_count: 8,
    error: null,
    tags: [],
    eval_score: null,
  };
}

const DIFF: DiffView = {
  run_a: "run_a",
  run_b: "run_b",
  summary_a: summary("run_a", 0.08, 2700),
  summary_b: summary("run_b", 0.094, 2376),
  identical: false,
  dimensions: [
    { dimension: "model", changed: false, differences: 0 },
    { dimension: "prompt", changed: false, differences: 0 },
    { dimension: "context", changed: true, differences: 2 },
    { dimension: "memory", changed: false, differences: 0 },
    { dimension: "tools", changed: false, differences: 0 },
    { dimension: "mcp", changed: false, differences: 0 },
  ],
  differences: [
    {
      dimension: "context",
      field: "skill:refund-policy",
      kind: "changed",
      before: "18",
      after: "19",
      delta: null,
      description: "context.skill:refund-policy: '18' -> '19'",
    },
  ],
  explanations: [
    {
      observed: "output",
      cause: "context",
      confidence: 0.62,
      rationale: "the retrieved refund policy changed and nothing else did",
      description: "output may have changed because context changed",
    },
  ],
  output_changed: true,
  cost_delta_percent: 17.5,
  latency_delta_percent: -12,
  quality_delta: null,
};

beforeEach(() => {
  clearCache();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(DIFF), { status: 200 })),
  );
  window.history.replaceState({}, "", "/compare?a=run_a&b=run_b");
});

afterEach(() => vi.unstubAllGlobals());

function view() {
  return render(
    <RouterProvider>
      <CompareWorkspace a="run_a" b="run_b" />
    </RouterProvider>,
  );
}

describe("the compare workspace (UI §22, §23)", () => {
  it("answers what changed before anything else", async () => {
    view();
    await waitFor(() => expect(screen.getByText("What changed?")).toBeDefined());
    const table = screen.getByText("What changed?").nextElementSibling as HTMLElement;
    expect(table.textContent).toContain("Model");
    expect(table.textContent).toContain("Same");
    expect(table.textContent).toContain("Changed (2)");
  });

  it("shows cost and latency as deltas, in the direction that matters", async () => {
    view();
    await waitFor(() => expect(screen.getByText("+17.5%")).toBeDefined());
    // A cost increase reads as bad, a latency decrease as good.
    expect(screen.getByText("+17.5%").getAttribute("style")).toContain("--fail");
    expect(screen.getByText("-12%").getAttribute("style")).toContain("--ok");
  });

  it("never presents a hypothesis as a fact", async () => {
    view();
    await waitFor(() => expect(screen.getByText(/^Observed \(/)).toBeDefined());
    expect(screen.getByText("Inference")).toBeDefined();
    expect(screen.getByText("62.0% confidence")).toBeDefined();
    expect(
      screen.getByText(/Observed changes are facts read from the two recordings/),
    ).toBeDefined();
  });

  it("says plainly when a run has not been evaluated", async () => {
    view();
    await waitFor(() =>
      expect(screen.getByText(/not evaluated — save these runs as tests/)).toBeDefined(),
    );
  });
});
