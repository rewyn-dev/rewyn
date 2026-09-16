import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { IncidentDetail } from "@/api/types";
import { IncidentScreen } from "@/components/incidents/IncidentScreens";
import { clearCache } from "@/hooks/useApi";
import { RouterProvider } from "@/lib/router";

const STAGES = [
  "deployment",
  "behavior_change",
  "detection",
  "investigation",
  "fix",
  "regression",
  "resolved",
];

const INCIDENT: IncidentDetail = {
  id: "errors-abc123",
  severity: "critical",
  summary: "12 runs failing: ToolError: salesforce timeout",
  detail: "ToolError: salesforce timeout",
  run_ids: [],
  started_at: "2026-09-13T10:41:00Z",
  affected_runs: 12,
  likely_cause: "all failures are in refund-agent",
  agent: "refund-agent",
  environment: "production",
  status: "open",
  assignee: null,
  first_seen: "2026-09-13T10:41:00Z",
  last_seen: "2026-09-13T11:02:00Z",
  cause_evidence: [
    "Observed: all failures are in refund-agent.",
    "Inference: that change is the strongest candidate — not a confirmed cause.",
  ],
  timeline: STAGES.map((stage, index) => ({
    stage,
    reached: index < 3,
    at: index < 3 ? "2026-09-13T10:41:00Z" : null,
    summary: index < 3 ? `${stage} happened` : `nothing has reached ${stage} yet`,
    evidence: index < 3 ? "recorded in run run_1" : "",
    href: index < 3 ? "/runs/run_1" : null,
  })),
  runs: [],
  comments: [],
  note: "",
};

beforeEach(() => {
  clearCache();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/comments")) return new Response("[]", { status: 200 });
      return new Response(JSON.stringify(INCIDENT), { status: 200 });
    }),
  );
  window.history.replaceState({}, "", "/incidents/errors-abc123");
});

afterEach(() => vi.unstubAllGlobals());

function view() {
  return render(
    <RouterProvider>
      <IncidentScreen id="errors-abc123" />
    </RouterProvider>,
  );
}

describe("the incident view (UI §36)", () => {
  it("prints all seven stages, including the ones nothing has reached", async () => {
    view();
    await waitFor(() => expect(screen.getByText("Timeline")).toBeDefined());
    for (const label of [
      "Deployment",
      "Behavior change",
      "Detection",
      "Investigation",
      "Fix",
      "Regression",
      "Resolved",
    ]) {
      expect(screen.getByText(label)).toBeDefined();
    }
    // The unreached ones are marked, not hidden: that is the to-do list.
    expect(screen.getAllByText("not yet")).toHaveLength(4);
  });

  it("leads with the numbers §36 prints", async () => {
    view();
    await waitFor(() => expect(screen.getByText("Affected runs")).toBeDefined());
    expect(screen.getByText("12")).toBeDefined();
    expect(screen.getByText("unassigned")).toBeDefined();
  });

  it("labels a cause as observation or inference, never as fact", async () => {
    view();
    await waitFor(() => expect(screen.getByText("Likely cause")).toBeDefined());
    expect(screen.getByText("Observed")).toBeDefined();
    expect(screen.getByText("Inference")).toBeDefined();
    expect(screen.getByText(/not a confirmed cause/)).toBeDefined();
  });

  it("says what would reach an unreached stage", async () => {
    view();
    await waitFor(() => expect(screen.getByText("Timeline")).toBeDefined());
    expect(screen.getByText("nothing has reached regression yet")).toBeDefined();
  });
});
