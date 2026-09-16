import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RunSummary } from "@/api/types";
import { RunsTable } from "@/components/runs/RunsTable";
import { RouterProvider } from "@/lib/router";

function run(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    id: "run_01ABCDEF",
    name: "refund",
    status: "failed",
    project: "default",
    environment: "production",
    agent: "refund-agent",
    user: "sam",
    model: "claude-x",
    session_id: null,
    parent_run_id: null,
    started_at: "2026-09-13T10:41:00Z",
    ended_at: "2026-09-13T10:41:04Z",
    duration_ms: 4820,
    cost: 0.19,
    input_tokens: 1200,
    output_tokens: 300,
    model_calls: 2,
    tool_calls: 1,
    event_count: 24,
    error: "ToolError: salesforce timeout",
    tags: [],
    eval_score: null,
    ...overrides,
  };
}

describe("the runs table (UI §7)", () => {
  it("shows the columns the spec lists", () => {
    render(
      <RouterProvider>
        <RunsTable runs={[run()]} />
      </RouterProvider>,
    );
    for (const header of ["Time", "Agent", "User", "Model", "Latency", "Cost"]) {
      expect(screen.getByRole("columnheader", { name: header })).toBeDefined();
    }
    expect(screen.getByText("refund-agent")).toBeDefined();
    expect(screen.getByText("sam")).toBeDefined();
    expect(screen.getByText("claude-x")).toBeDefined();
    expect(screen.getByText("4.82s")).toBeDefined();
    expect(screen.getByText("$0.19")).toBeDefined();
  });

  it("puts the failure where the eye lands, not in a tooltip only", () => {
    render(
      <RouterProvider>
        <RunsTable runs={[run()]} />
      </RouterProvider>,
    );
    expect(screen.getByText(/salesforce timeout/)).toBeDefined();
    expect(screen.getByRole("img", { name: "failed" })).toBeDefined();
  });

  it("is reachable by keyboard", () => {
    render(
      <RouterProvider>
        <RunsTable runs={[run()]} />
      </RouterProvider>,
    );
    const [row] = screen.getAllByRole("row").slice(1);
    expect(row?.getAttribute("tabindex")).toBe("0");
  });
});
