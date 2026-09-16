import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Onboarding } from "@/api/types";
import { WelcomeScreen } from "@/components/onboarding/Welcome";
import { clearCache } from "@/hooks/useApi";
import { RouterProvider } from "@/lib/router";

const EMPTY: Onboarding = {
  location: {
    surface: "local",
    project: "default",
    home: "/work/app/.rewyn",
    endpoint: null,
    how_to_change:
      "A local project is a directory. There is nothing to create: record a run and the project exists.",
  },
  counts: { runs: 0, agents: 0, datasets: 0, reports: 0, failures: 0, versions: 0 },
  empty: true,
  demo_loaded: false,
  can_load_demo: true,
  summary: "Rewyn records what your AI actually did.",
  steps: [
    {
      key: "install",
      title: "Install the SDK",
      detail: "It ships with this console.",
      done: true,
      code: "pip install rewyn",
      href: null,
      action: "",
    },
    {
      key: "record",
      title: "Record your first run",
      detail: "Wrap an agent and run it.",
      done: false,
      code: "agent.run('hello')",
      href: null,
      action: "",
    },
  ],
  jobs: [
    {
      key: "debug",
      title: "Debug an answer you do not trust",
      question: "What is my AI doing?",
      detail: "Read the timeline.",
      href: "/runs",
      ready: false,
      needs: "Record a run.",
    },
  ],
};

beforeEach(() => {
  clearCache();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(EMPTY), { status: 200 })),
  );
  window.history.replaceState({}, "", "/welcome");
});

afterEach(() => vi.unstubAllGlobals());

function view() {
  return render(
    <RouterProvider>
      <WelcomeScreen />
    </RouterProvider>,
  );
}

describe("the first screen (UI §47)", () => {
  it("says what the tool is before asking for anything", async () => {
    view();
    await waitFor(() =>
      expect(screen.getByText("Rewyn records what your AI actually did.")).toBeDefined(),
    );
  });

  it("says where it is reading from and that a project is not created here", async () => {
    view();
    await waitFor(() => expect(screen.getByText("/work/app/.rewyn")).toBeDefined());
    expect(screen.getByText(/nothing to create/)).toBeDefined();
  });

  it("hands you something that runs, rather than a placeholder", async () => {
    view();
    await waitFor(() => expect(screen.getByText("Record your first run")).toBeDefined());
    expect(screen.getByText("agent.run('hello')")).toBeDefined();
  });

  it("marks what is already done rather than restating the whole tutorial", async () => {
    view();
    await waitFor(() => expect(screen.getByText("Install the SDK")).toBeDefined());
    expect(screen.getAllByText("done")).toHaveLength(1);
  });

  it("offers the demo when there is nothing to look at", async () => {
    view();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Load the demo project" })).toBeDefined(),
    );
  });

  it("says what an unready job still needs", async () => {
    view();
    await waitFor(() => expect(screen.getByText(/Needs: Record a run/)).toBeDefined());
  });
});
