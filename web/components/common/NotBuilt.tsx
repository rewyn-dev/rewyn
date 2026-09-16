"use client";

/**
 * The empty state for a navigation entry whose screen is not built yet
 * (UI spec §47): it teaches what the screen will answer and where the work
 * sits in the plan, rather than pretending or 404ing.
 */

import { Empty } from "./atoms";
import { Link } from "@/lib/router";
import type { NavEntry } from "./nav";

export function NotBuilt({ entry }: { entry: NavEntry }) {
  return (
    <div className="page">
      <Empty title={`${entry.label} is not built yet`}>
        <p>
          This screen will answer: <strong>{entry.question}</strong>
        </p>
        <p>
          It ships in <strong>{entry.phase}</strong>. Until then the data behind it is already being
          recorded, so nothing is lost.
        </p>
      </Empty>
      <div style={{ textAlign: "center" }}>
        <Link className="btn" href="/runs">
          Open runs
        </Link>
      </div>
    </div>
  );
}
