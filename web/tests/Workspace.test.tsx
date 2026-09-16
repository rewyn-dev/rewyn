import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkspaceView } from "@/api/types";
import { WorkspaceScreen } from "@/components/workspace/Workspace";
import { clearCache } from "@/hooks/useApi";
import { RouterProvider } from "@/lib/router";

function stage(key: string, label: string, status: "ok" | "idle" | "fail") {
  return {
    key,
    label,
    question: `${label}?`,
    ready: status !== "idle",
    href: `/${key}`,
    summary: status === "idle" ? "" : `${label} is done`,
    detail: status === "idle" ? `Nothing has started ${label}.` : "",
    count: status === "idle" ? null : 3,
    status,
  };
}

const VIEW: WorkspaceView = {
  agent: "refund-agent",
  version: "3",
  environment: "production",
  components: [
    { kind: "model", name: "gpt-5", version: "1", href: "/models/gpt-5" },
    { kind: "skill", name: "refund-policy", version: "19", href: "/skills/refund-policy" },
  ],
  stages: [
    stage("config", "Agent configuration", "ok"),
    stage("run", "Run", "ok"),
    stage("regression", "Regression", "idle"),
  ],
  loop: [
    stage("build", "Build", "ok"),
    stage("release", "Release", "fail"),
    stage("monitor", "Monitor", "ok"),
  ],
  latest_run: null,
  recent_runs: [],
  incidents: [],
  next_step: "Regression: Nothing has started Regression.",
};

beforeEach(() => {
  clearCache();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/agents")) return new Response("[]", { status: 200 });
      if (url.includes("/comments")) return new Response("[]", { status: 200 });
      return new Response(JSON.stringify(VIEW), { status: 200 });
    }),
  );
  window.history.replaceState({}, "", "/workspace?agent=refund-agent");
});

afterEach(() => vi.unstubAllGlobals());

function view(agent: string | null = "refund-agent") {
  return render(
    <RouterProvider>
      <WorkspaceScreen agent={agent} />
    </RouterProvider>,
  );
}

describe("the AI development workspace (UI §37, §60)", () => {
  it("puts the whole lifecycle in one frame", async () => {
    view();
    await waitFor(() => expect(screen.getByText("Lifecycle")).toBeDefined());
    expect(screen.getByText("Configuration")).toBeDefined();
    expect(screen.getByText("Agent configuration")).toBeDefined();
    expect(screen.getByText("gpt-5")).toBeDefined();
  });

  it("says what to do next rather than leaving it to be worked out", async () => {
    view();
    await waitFor(() =>
      expect(screen.getByText("Regression: Nothing has started Regression.")).toBeDefined(),
    );
    expect(screen.getByText("Nothing has started Regression.")).toBeDefined();
  });

  it("makes every step of the loop a link", async () => {
    view();
    const loop = await waitFor(() => screen.getByLabelText("Development loop"));
    const steps = loop.querySelectorAll("a");
    expect(steps).toHaveLength(3);
    expect([...steps].map((step) => step.getAttribute("href"))).toEqual([
      "/build",
      "/release",
      "/monitor",
    ]);
    expect([...steps].map((step) => step.getAttribute("data-status"))).toEqual([
      "ok",
      "fail",
      "ok",
    ]);
  });

  it("asks for an agent before it claims anything about one", async () => {
    view(null);
    await waitFor(() => expect(screen.getByText("Pick an agent")).toBeDefined());
  });
});
