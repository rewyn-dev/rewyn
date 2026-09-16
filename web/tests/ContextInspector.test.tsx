import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ContextView } from "@/api/types";
import { ContextInspector } from "@/components/context/ContextInspector";
import { clearCache } from "@/hooks/useApi";

const VIEW: ContextView = {
  run_id: "run_1",
  retrievals: [],
  assemblies: [
    {
      seq: 4,
      context: "context",
      version: "1",
      query: "refund policy",
      fingerprint: "fp",
      budget: 16_000,
      used: 9_200,
      by_kind: { instructions: 1_200, knowledge: 6_200, memory: 1_800 },
      dropped_untrusted: ["ctx_spam"],
      stale: [],
      assembled_in_ms: 4.2,
      items: [
        {
          id: "ctx_policy",
          kind: "knowledge",
          title: "Refund Policy",
          source: "Google Drive",
          record: "policies/refund",
          version: "19",
          uri: "https://drive.example.com/refund",
          retrieved_at: "2026-09-13T08:00:00Z",
          hash: "sha256:abc123def456",
          tokens: 1_824,
          relevance: 0.91,
          authority: 0.9,
          trust_level: "trusted",
          sensitivity: "internal",
          verified: true,
          included: true,
          excluded_reason: null,
          redacted: false,
          redaction_reason: null,
        },
        {
          id: "ctx_secret",
          kind: "knowledge",
          title: null,
          source: null,
          record: null,
          version: "2",
          uri: null,
          retrieved_at: null,
          hash: null,
          tokens: 400,
          relevance: 0.4,
          authority: 0.5,
          trust_level: "internal",
          sensitivity: "restricted",
          verified: null,
          included: true,
          excluded_reason: null,
          redacted: true,
          redaction_reason: "sensitivity 'restricted' exceeds your access",
        },
        {
          id: "ctx_old",
          kind: "history",
          title: "Old thread",
          source: "slack",
          record: null,
          version: null,
          uri: null,
          retrieved_at: null,
          hash: null,
          tokens: 900,
          relevance: 0.1,
          authority: 0.4,
          trust_level: "untrusted",
          sensitivity: "internal",
          verified: null,
          included: false,
          excluded_reason: "over budget",
          redacted: false,
          redaction_reason: null,
        },
      ],
    },
  ],
};

beforeEach(() => {
  clearCache();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(VIEW), { status: 200 })),
  );
});

afterEach(() => vi.unstubAllGlobals());

describe("the context inspector (UI §11, §12, §54)", () => {
  it("shows what consumed the context window", async () => {
    render(<ContextInspector runId="run_1" />);
    await waitFor(() => expect(screen.getByText("9,200")).toBeDefined());
    expect(screen.getByText(/of 16,000 tokens used/)).toBeDefined();
    // One legend entry per kind, so the bar is readable without hovering.
    expect(screen.getAllByText("knowledge").length).toBeGreaterThan(0);
    expect(screen.getByText("6,200")).toBeDefined();
  });

  it("shows source, version, tokens and provenance per item", async () => {
    render(<ContextInspector runId="run_1" />);
    await waitFor(() => expect(screen.getByText("Refund Policy")).toBeDefined());
    expect(screen.getByText("v19")).toBeDefined();
    expect(screen.getByText("Google Drive")).toBeDefined();
    expect(screen.getByText("1,824")).toBeDefined();
    expect(screen.getByRole("link", { name: "View source" })).toBeDefined();
  });

  it("reports a withheld item instead of hiding that it existed", async () => {
    render(<ContextInspector runId="run_1" />);
    await waitFor(() => expect(screen.getByText("Withheld")).toBeDefined());
    expect(screen.getByText(/exceeds your access/)).toBeDefined();
  });

  it("separates what was dropped, and why", async () => {
    render(<ContextInspector runId="run_1" />);
    await waitFor(() => expect(screen.getByText("Excluded (1)")).toBeDefined());
    expect(screen.getByText(/excluded: over budget/)).toBeDefined();
    expect(screen.getByText(/1 untrusted item\(s\) dropped/)).toBeDefined();
  });
});
