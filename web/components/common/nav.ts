/**
 * The primary navigation (UI spec §4), printed exactly as the spec lists it.
 *
 * Entries whose screens have not been built yet are still present: the
 * navigation is the map of the product, and a missing entry would misdescribe
 * it. Opening one shows what it will hold and which phase builds it (UI §47),
 * never a fabricated screen.
 */

export interface NavEntry {
  label: string;
  href: string;
  ready: boolean;
  /** What this screen answers, from the UX north star (UI §59). */
  question: string;
  phase?: string;
  /**
   * What this screen needs before it can answer its question.
   *
   * The entry is never hidden — the navigation is the map of the product
   * (UI §4), and a map that erases the places you have not been to yet is a
   * worse map. It is marked instead, with the one thing that unlocks it
   * (UI §47).
   */
  needs?: Requirement;
}

export type Requirement = "runs" | "agents" | "datasets" | "reports" | "versions";

export const UNLOCKS: Record<Requirement, string> = {
  runs: "Record a run",
  agents: "Run an agent",
  datasets: "Save a run as a test",
  reports: "Run a regression test",
  versions: "Record two versions of an agent",
};

export interface NavGroup {
  label: string | null;
  items: NavEntry[];
}

export const NAV: NavGroup[] = [
  {
    label: null,
    items: [{ label: "Overview", href: "/", ready: true, question: "Is my AI system healthy?" }],
  },
  {
    label: "Build",
    items: [
      {
        label: "Agents",
        href: "/agents",
        ready: true,
        question: "What is my AI made of?",
        needs: "agents",
      },
      {
        label: "Models",
        href: "/models",
        ready: true,
        question: "Which models am I running on?",
        needs: "runs",
      },
      {
        label: "Prompts",
        href: "/prompts",
        ready: true,
        question: "What am I asking the model?",
        needs: "runs",
      },
      {
        label: "Skills",
        href: "/skills",
        ready: true,
        question: "Which skills exist, at which version?",
        needs: "runs",
      },
      {
        label: "Tools",
        href: "/tools",
        ready: true,
        question: "What can my agents actually do?",
        needs: "runs",
      },
      {
        label: "MCP",
        href: "/mcp",
        ready: true,
        question: "Which servers am I depending on?",
        needs: "runs",
      },
      {
        label: "Context",
        href: "/context",
        ready: true,
        question: "How is context assembled?",
        needs: "runs",
      },
      {
        label: "Memory",
        href: "/memory",
        ready: true,
        question: "What does the system remember?",
        needs: "runs",
      },
      {
        label: "Graphs",
        href: "/graphs",
        ready: true,
        question: "What is the control flow?",
        needs: "runs",
      },
    ],
  },
  {
    label: "Run",
    items: [
      {
        label: "Runs",
        href: "/runs",
        ready: true,
        question: "What is my AI doing?",
        needs: "runs",
      },
      {
        label: "Sessions",
        href: "/sessions",
        ready: true,
        question: "What happened in this conversation?",
        needs: "runs",
      },
      { label: "Live", href: "/live", ready: true, question: "What is running right now?" },
    ],
  },
  {
    label: "Quality",
    items: [
      {
        label: "Evaluations",
        href: "/evaluations",
        ready: true,
        question: "Is it good?",
        needs: "runs",
      },
      {
        label: "Datasets",
        href: "/datasets",
        ready: true,
        question: "What do I test against?",
        needs: "datasets",
      },
      {
        label: "Regression",
        href: "/regression",
        ready: true,
        question: "Did my change make it worse?",
        needs: "reports",
      },
      {
        label: "Experiments",
        href: "/experiments",
        ready: true,
        question: "Which variant wins?",
        needs: "reports",
      },
    ],
  },
  {
    label: "Intelligence",
    items: [
      {
        label: "Replay",
        href: "/replay",
        ready: true,
        question: "Can I reproduce it?",
        needs: "runs",
      },
      { label: "Compare", href: "/compare", ready: true, question: "What changed?", needs: "runs" },
      {
        label: "Drift",
        href: "/drift",
        ready: true,
        question: "Is behaviour moving?",
        needs: "versions",
      },
      {
        label: "Dependencies",
        href: "/dependencies",
        ready: true,
        question: "What could have changed outside my code?",
        needs: "runs",
      },
    ],
  },
  {
    label: "Project",
    items: [
      {
        label: "Releases",
        href: "/releases",
        ready: true,
        question: "Is it safe to deploy?",
        needs: "agents",
      },
      {
        label: "Environments",
        href: "/environments",
        ready: true,
        question: "Where is this running?",
        needs: "runs",
      },
      {
        label: "Settings",
        href: "/settings",
        ready: true,
        question: "What is this console connected to?",
      },
    ],
  },
];

export function findEntry(path: string): NavEntry | null {
  for (const group of NAV) {
    for (const item of group.items) {
      if (item.href === path) return item;
    }
  }
  return SECONDARY.find((item) => item.href === path) ?? null;
}

/**
 * The two screens UI §36 and §37 name, which §4's sidebar predates.
 *
 * §4 prints its list exactly and asks the navigation to stay compact, so
 * neither was folded into a group there: a screen the spec names by section
 * still needs a way in, and a separate footer group is the honest way to add
 * one without quietly editing the map of the product. `NAV` above remains
 * the spec's list, character for character.
 */
export const SECONDARY: NavEntry[] = [
  {
    label: "Workspace",
    href: "/workspace",
    ready: true,
    question: "Where am I in the loop?",
  },
  {
    label: "Incidents",
    href: "/incidents",
    ready: true,
    question: "What is broken, and since when?",
  },
];

export function findSecondary(path: string): NavEntry | null {
  return SECONDARY.find((item) => item.href === path) ?? null;
}

/**
 * The §60 loop, as command-palette entries.
 *
 * The gate on this phase is that the loop is navigable without leaving the
 * UI. The workspace draws it for one agent; these make every step reachable
 * from any screen with ⌘P, which is the part that matters when you are three
 * pages deep in a run and want the next step rather than the map.
 */
export const LOOP_COMMANDS: { label: string; href: string; question: string }[] = [
  { label: "Build: open the workspace", href: "/workspace", question: "What is my AI made of?" },
  { label: "Debug: open runs", href: "/runs", question: "What is my AI doing?" },
  { label: "Replay: pick a run", href: "/replay", question: "Can I reproduce it?" },
  {
    label: "Experiment: compare variants",
    href: "/experiments",
    question: "Which variant wins?",
  },
  {
    label: "Evaluate: open evaluations",
    href: "/evaluations",
    question: "Is it good?",
  },
  {
    label: "Regression test: open regression",
    href: "/regression",
    question: "Did my change make it worse?",
  },
  {
    label: "Release: open releases",
    href: "/releases",
    question: "Is it safe to deploy?",
  },
  { label: "Monitor: open incidents", href: "/incidents", question: "What is broken?" },
  {
    label: "Learn: open dependencies",
    href: "/dependencies",
    question: "What could have changed outside my code?",
  },
];
