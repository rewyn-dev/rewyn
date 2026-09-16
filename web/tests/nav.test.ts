import { describe, expect, it } from "vitest";

import { LOOP_COMMANDS, NAV, SECONDARY, UNLOCKS, findEntry } from "@/components/common/nav";

/**
 * UI §4 prints the navigation exactly. This test is the guard that it stays
 * that way: an entry quietly dropped because its screen is unbuilt would
 * misdescribe the product.
 */
const SPEC = [
  [null, ["Overview"]],
  [
    "Build",
    ["Agents", "Models", "Prompts", "Skills", "Tools", "MCP", "Context", "Memory", "Graphs"],
  ],
  ["Run", ["Runs", "Sessions", "Live"]],
  ["Quality", ["Evaluations", "Datasets", "Regression", "Experiments"]],
  ["Intelligence", ["Replay", "Compare", "Drift", "Dependencies"]],
  ["Project", ["Releases", "Environments", "Settings"]],
] as const;

describe("primary navigation", () => {
  it("matches the spec, group for group and item for item", () => {
    expect(NAV.map((group) => [group.label, group.items.map((item) => item.label)])).toEqual(
      SPEC.map(([label, items]) => [label, [...items]]),
    );
  });

  it("says which phase builds each screen that is not ready", () => {
    for (const group of NAV) {
      for (const item of group.items) {
        expect(item.question.length).toBeGreaterThan(0);
        if (!item.ready) expect(item.phase).toBeTruthy();
      }
    }
  });
});

describe("the P4 screens the spec names but §4 predates", () => {
  it("sits outside the spec's groups rather than inside them", () => {
    // The guard above is the point: §36 and §37 get an entry without §4's
    // list being quietly edited to make room.
    expect(SECONDARY.map((item) => item.label)).toEqual(["Workspace", "Incidents"]);
    for (const group of NAV) {
      expect(group.items.map((item) => item.label)).not.toContain("Incidents");
      expect(group.items.map((item) => item.label)).not.toContain("Workspace");
    }
  });

  it("is still findable, so a deep link is not a dead end", () => {
    expect(findEntry("/incidents")?.label).toBe("Incidents");
    expect(findEntry("/workspace")?.label).toBe("Workspace");
  });
});

describe("the §60 loop", () => {
  it("is reachable from any screen through the command palette", () => {
    expect(LOOP_COMMANDS).toHaveLength(9);
    expect(LOOP_COMMANDS.map((entry) => entry.label.split(":")[0])).toEqual([
      "Build",
      "Debug",
      "Replay",
      "Experiment",
      "Evaluate",
      "Regression test",
      "Release",
      "Monitor",
      "Learn",
    ]);
    for (const entry of LOOP_COMMANDS) {
      expect(entry.href.startsWith("/")).toBe(true);
      expect(entry.question.endsWith("?")).toBe(true);
    }
  });
});

describe("what a screen needs before it can answer (UI §47)", () => {
  it("never hides an entry, only marks it", () => {
    // The sidebar is the map of the product. A map that erases the places you
    // have not been to yet is a worse map, so `needs` is a label, not a filter.
    const all = [...NAV.flatMap((group) => group.items), ...SECONDARY];
    expect(all.length).toBeGreaterThan(20);
    for (const item of all) {
      if (item.needs) expect(UNLOCKS[item.needs]).toBeTruthy();
    }
  });

  it("asks for two versions before claiming drift, not two runs", () => {
    const drift = NAV.flatMap((group) => group.items).find((item) => item.href === "/drift");
    expect(drift?.needs).toBe("versions");
  });

  it("gates the quality screens on the thing that actually fills them", () => {
    const byHref = new Map(
      NAV.flatMap((group) => group.items).map((item) => [item.href, item.needs]),
    );
    expect(byHref.get("/datasets")).toBe("datasets");
    expect(byHref.get("/regression")).toBe("reports");
    expect(byHref.get("/runs")).toBe("runs");
  });
});
