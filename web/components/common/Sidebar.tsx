"use client";

/** The primary navigation (UI spec §4) and the project selector (UI §41). */

import type { Capabilities, ConsoleCounts } from "@/api/types";
import { Link, useRouter } from "@/lib/router";
import { NAV, SECONDARY, UNLOCKS } from "./nav";
import type { NavEntry } from "./nav";

/**
 * Whether a screen can answer its question yet.
 *
 * An entry is never hidden: UI §4's sidebar is the map of the product, and a
 * map that erases the places you have not been to is a worse map. It is
 * marked instead, so a new project reads as "not yet" rather than "broken".
 */
function unlocked(entry: NavEntry, counts: ConsoleCounts | undefined): boolean {
  if (!entry.needs || !counts) return true;
  if (entry.needs === "versions") return counts.versions > 1;
  return counts[entry.needs] > 0;
}

export function Sidebar({ capabilities }: { capabilities: Capabilities | null }) {
  const { path } = useRouter();
  const counts = capabilities?.counts;
  const location = capabilities?.location;

  return (
    <nav className="sidebar" aria-label="Primary">
      <div className="brand">
        <span className="brand-dot" aria-hidden="true" />
        Rewyn
      </div>
      <Link className="project" href="/welcome" title={location?.how_to_change}>
        <div className="project-name">{capabilities?.project ?? "…"}</div>
        <div className="project-where">
          {capabilities
            ? capabilities.surface === "local"
              ? (location?.home ?? "local project")
              : `cloud · ${capabilities.role}`
            : ""}
        </div>
        {capabilities?.demo_loaded ? <span className="tag tag-warn">demo data</span> : null}
      </Link>
      {[...NAV, { label: null, items: SECONDARY }].map((group) => (
        <div className="nav-group" key={group.label ?? group.items[0]?.href ?? "root"}>
          {group.label ? <div className="nav-label">{group.label}</div> : null}
          {group.items.map((item) => {
            const current = item.href === "/" ? path === "/" : path.startsWith(item.href);
            const ready = unlocked(item, counts);
            return (
              <Link
                key={item.href}
                href={item.href}
                className="nav-item"
                aria-current={current ? "page" : undefined}
                data-locked={!ready}
                title={ready ? item.question : `${UNLOCKS[item.needs!]} to use this`}
              >
                <span>{item.label}</span>
                {!item.ready ? <span className="count">{item.phase}</span> : null}
                {item.ready && !ready ? (
                  // Visible, so "not yet" is not signalled by dimming alone,
                  // but hidden from the accessible name: the entry is still
                  // called "Datasets", and the title carries the reason.
                  <span className="count" aria-hidden="true">
                    ·
                  </span>
                ) : null}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
