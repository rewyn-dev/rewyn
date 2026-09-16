"""A queryable index over local runs (UI spec §7, §50).

The runs page filters by fourteen dimensions and has to stay fast with a
hundred thousand runs, which rules out reading every manifest on every
request. This module keeps a small SQLite index beside the run directory --
one row per run, plus one row per dependency for the facet filters -- and
refreshes it incrementally from file modification times.

The index is a cache, never a source of truth: deleting ``.rewyn/ui`` costs
one rebuild and nothing else. Everything it stores is derived from manifests,
which is why indexing never reads an event log (UI §50).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rewyn.core.run import RunManifest
from rewyn.replay.recorder import RecordedRun
from rewyn.storage.local import LocalStore
from rewyn.ui import projections as p
from rewyn.ui import schemas as s

INDEX_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    mtime         REAL NOT NULL,
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    name          TEXT NOT NULL,
    status        TEXT NOT NULL,
    project       TEXT NOT NULL,
    environment   TEXT NOT NULL,
    agent         TEXT,
    user          TEXT,
    model         TEXT,
    session_id    TEXT,
    parent_run_id TEXT,
    duration_ms   REAL NOT NULL DEFAULT 0,
    cost          REAL NOT NULL DEFAULT 0,
    event_count   INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    eval_score    REAL,
    cost_model     REAL NOT NULL DEFAULT 0,
    cost_tool      REAL NOT NULL DEFAULT 0,
    cost_embedding REAL NOT NULL DEFAULT 0,
    cost_retrieval REAL NOT NULL DEFAULT 0,
    cost_sandbox   REAL NOT NULL DEFAULT 0,
    search_text   TEXT NOT NULL DEFAULT '',
    summary       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS runs_started ON runs (started_at DESC, run_id DESC);
CREATE INDEX IF NOT EXISTS runs_status ON runs (status);
CREATE INDEX IF NOT EXISTS runs_agent ON runs (agent);
CREATE INDEX IF NOT EXISTS runs_environment ON runs (environment);
CREATE INDEX IF NOT EXISTS runs_session ON runs (session_id);

CREATE TABLE IF NOT EXISTS facets (
    run_id  TEXT NOT NULL,
    kind    TEXT NOT NULL,
    name    TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT 'unversioned',
    PRIMARY KEY (run_id, kind, name)
);

CREATE INDEX IF NOT EXISTS facets_lookup ON facets (kind, name);

CREATE TABLE IF NOT EXISTS tags (
    run_id TEXT NOT NULL,
    tag    TEXT NOT NULL,
    PRIMARY KEY (run_id, tag)
);

-- Scores are recorded on the evaluating run, so they are stored against the
-- run they judged and joined back onto it (UI §7's evaluation-score filter).
CREATE TABLE IF NOT EXISTS scores (
    subject_run_id TEXT NOT NULL,
    evaluator      TEXT NOT NULL,
    scored_in      TEXT NOT NULL,
    value          REAL NOT NULL DEFAULT 0,
    passed         INTEGER NOT NULL DEFAULT 0,
    scored_at      TEXT NOT NULL,
    PRIMARY KEY (subject_run_id, evaluator, scored_in)
);

CREATE INDEX IF NOT EXISTS scores_subject ON scores (subject_run_id);
CREATE INDEX IF NOT EXISTS scores_source ON scores (scored_in);
"""

# Which dependency kind backs which §7 filter.
FACET_KINDS: dict[str, tuple[str, ...]] = {
    "tool": ("tool",),
    "mcp": ("mcp_server", "mcp"),
    "skill": ("skill",),
    "model": ("model",),
    "agent": ("agent", "graph"),
    "context": ("context",),
    "memory": ("memory",),
    "dataset": ("dataset",),
    "retriever": ("retriever",),
}


@dataclass(slots=True)
class RunQuery:
    """The runs-page filter set (UI §7). Every field is optional."""

    q: str | None = None
    project: str | None = None
    agent: str | None = None
    model: str | None = None
    status: str | None = None
    user: str | None = None
    environment: str | None = None
    session_id: str | None = None
    tool: str | None = None
    mcp: str | None = None
    skill: str | None = None
    tag: str | None = None
    error: bool | None = None
    since: datetime | None = None
    until: datetime | None = None
    min_cost: float | None = None
    max_cost: float | None = None
    min_latency_ms: float | None = None
    max_latency_ms: float | None = None
    min_score: float | None = None
    max_score: float | None = None
    limit: int = 50
    cursor: str | None = None
    order: str = "started_at"
    extras: dict[str, Any] = field(default_factory=dict)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def encode_cursor(summary: s.RunSummary) -> str:
    return f"{summary.started_at.isoformat()}|{summary.id}"


def decode_cursor(cursor: str) -> tuple[str, str]:
    started, _, run_id = cursor.partition("|")
    return started, run_id


class RunIndex:
    """Index and query the runs under a :class:`LocalStore`."""

    def __init__(self, store: LocalStore | None = None, *, path: Path | None = None) -> None:
        self.store = store or LocalStore()
        self.path = path or (self.store.home / "ui" / "index.db")
        self._ensure_schema()

    # Connection --------------------------------------------------------------
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            row = connection.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO meta (key, value) VALUES ('version', ?)", (str(INDEX_VERSION),)
                )
            elif row["value"] != str(INDEX_VERSION):
                connection.executescript(
                    "DROP TABLE IF EXISTS runs; DROP TABLE IF EXISTS facets; "
                    "DROP TABLE IF EXISTS tags; DROP TABLE IF EXISTS scores;" + SCHEMA
                )
                connection.execute(
                    "REPLACE INTO meta (key, value) VALUES ('version', ?)", (str(INDEX_VERSION),)
                )

    # Indexing ----------------------------------------------------------------
    def refresh(self, *, force: bool = False) -> int:
        """Index runs that are new or changed since last time. Returns the count."""
        runs_dir = self.store.runs_dir
        if not runs_dir.exists():
            return 0
        with self._connect() as connection:
            known = {
                row["run_id"]: row["mtime"]
                for row in connection.execute("SELECT run_id, mtime FROM runs")
            }
            seen: set[str] = set()
            indexed = 0
            for entry in runs_dir.iterdir():
                manifest_path = entry / "manifest.json"
                if not manifest_path.exists():
                    continue
                run_id = entry.name
                seen.add(run_id)
                mtime = manifest_path.stat().st_mtime
                if not force and known.get(run_id) == mtime:
                    continue
                try:
                    manifest = self.store.read_manifest(run_id)
                except (ValueError, OSError):
                    continue
                self._write(connection, manifest, mtime)
                indexed += 1
            for stale in set(known) - seen:
                self._delete(connection, stale)
            if indexed:
                self._join_scores(connection)
            return indexed

    def _delete(self, connection: sqlite3.Connection, run_id: str) -> None:
        connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
        connection.execute("DELETE FROM facets WHERE run_id = ?", (run_id,))
        connection.execute("DELETE FROM tags WHERE run_id = ?", (run_id,))
        connection.execute("DELETE FROM scores WHERE scored_in = ?", (run_id,))

    def _index_scores(self, connection: sqlite3.Connection, manifest: RunManifest) -> None:
        """Store the verdicts an evaluation run recorded, against their subjects.

        Only runs that used an evaluator are opened: indexing reads manifests,
        and an event log only when the manifest says there is something in it
        worth reading (UI §50).
        """
        if not any(d.kind == "evaluator" for d in manifest.dependencies):
            return
        try:
            events = self.store.read_events(manifest.id)
        except (OSError, ValueError):
            return
        connection.executemany(
            "INSERT OR REPLACE INTO scores "
            "(subject_run_id, evaluator, scored_in, value, passed, scored_at) "
            "VALUES (?,?,?,?,?,?)",
            [
                (
                    view.subject_run_id,
                    view.evaluator,
                    view.scored_in_run_id,
                    view.value,
                    int(view.passed),
                    view.scored_at.isoformat(),
                )
                for view in p.scores(RecordedRun(manifest, events))
            ],
        )

    def _join_scores(self, connection: sqlite3.Connection) -> None:
        """Copy the mean score onto each judged run, so a filter is one query."""
        connection.execute(
            """
            UPDATE runs SET eval_score = (
                SELECT AVG(value) FROM scores WHERE scores.subject_run_id = runs.run_id
            )
            WHERE EXISTS (SELECT 1 FROM scores WHERE scores.subject_run_id = runs.run_id)
            """
        )

    def scores_for(self, run_id: str) -> list[tuple[str, float, bool, str]]:
        """``(evaluator, value, passed, scored_in)`` for one run."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT evaluator, value, passed, scored_in FROM scores "
                "WHERE subject_run_id = ? ORDER BY evaluator",
                (run_id,),
            ).fetchall()
        return [(str(r[0]), float(r[1]), bool(r[2]), str(r[3])) for r in rows]

    def _write(self, connection: sqlite3.Connection, manifest: RunManifest, mtime: float) -> None:
        summary = p.run_summary(manifest, eval_score=_recorded_score(manifest))
        self._delete(connection, manifest.id)
        connection.execute(
            """
            INSERT INTO runs (
                run_id, mtime, started_at, ended_at, name, status, project, environment,
                agent, user, model, session_id, parent_run_id, duration_ms, cost,
                event_count, error, eval_score, cost_model, cost_tool, cost_embedding,
                cost_retrieval, cost_sandbox, search_text, summary
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                summary.id,
                mtime,
                summary.started_at.isoformat(),
                _iso(summary.ended_at),
                summary.name,
                summary.status,
                summary.project,
                summary.environment,
                summary.agent,
                summary.user,
                summary.model,
                summary.session_id,
                summary.parent_run_id,
                summary.duration_ms,
                summary.cost,
                summary.event_count,
                summary.error,
                summary.eval_score,
                manifest.cost.model,
                manifest.cost.tool,
                manifest.cost.embedding,
                manifest.cost.retrieval,
                manifest.cost.sandbox,
                _search_text(summary, manifest),
                summary.model_dump_json(),
            ),
        )
        connection.executemany(
            "INSERT OR REPLACE INTO facets (run_id, kind, name, version) VALUES (?,?,?,?)",
            [(manifest.id, d.kind, d.name, d.version) for d in manifest.dependencies],
        )
        connection.executemany(
            "INSERT OR REPLACE INTO tags (run_id, tag) VALUES (?,?)",
            [(manifest.id, tag) for tag in manifest.tags],
        )
        self._index_scores(connection, manifest)

    # Querying ----------------------------------------------------------------
    def search(self, query: RunQuery) -> s.RunPage:
        """Filter, sort and page the runs table (UI §7)."""
        where, params = _predicates(query)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        with self._connect() as connection:
            total_row = connection.execute(
                f"SELECT COUNT(*) AS n FROM runs{clause}", params
            ).fetchone()
            total = int(total_row["n"]) if total_row else 0
            page_where = list(where)
            page_params = list(params)
            if query.cursor:
                started, run_id = decode_cursor(query.cursor)
                page_where.append("(started_at, run_id) < (?, ?)")
                page_params.extend([started, run_id])
            page_clause = f" WHERE {' AND '.join(page_where)}" if page_where else ""
            rows = connection.execute(
                f"SELECT summary, eval_score FROM runs{page_clause} "
                "ORDER BY started_at DESC, run_id DESC LIMIT ?",
                [*page_params, query.limit + 1],
            ).fetchall()
        summaries = [_summary_of(row) for row in rows]
        has_more = len(summaries) > query.limit
        summaries = summaries[: query.limit]
        return s.RunPage(
            runs=summaries,
            total=total,
            next_cursor=encode_cursor(summaries[-1]) if has_more and summaries else None,
        )

    def get(self, run_id: str) -> s.RunSummary | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT summary, eval_score FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return _summary_of(row) if row else None

    def facets(self, *, project: str | None = None) -> s.RunFacets:
        """The filter vocabulary, with counts, for the runs page (UI §7)."""
        scope = "project = ? AND " if project else ""
        args: list[Any] = [project] if project else []
        with self._connect() as connection:

            def column(name: str) -> list[s.FacetValue]:
                rows = connection.execute(
                    f"SELECT {name} AS value, COUNT(*) AS n FROM runs "
                    f"WHERE {scope}{name} IS NOT NULL AND {name} != '' "
                    f"GROUP BY {name} ORDER BY n DESC LIMIT 100",
                    args,
                ).fetchall()
                return [s.FacetValue(value=str(r["value"]), count=int(r["n"])) for r in rows]

            def dependency(kinds: Sequence[str]) -> list[s.FacetValue]:
                marks = ",".join("?" for _ in kinds)
                rows = connection.execute(
                    f"SELECT name, COUNT(DISTINCT run_id) AS n FROM facets "
                    f"WHERE kind IN ({marks}) GROUP BY name ORDER BY n DESC LIMIT 100",
                    list(kinds),
                ).fetchall()
                return [s.FacetValue(value=str(r["name"]), count=int(r["n"])) for r in rows]

            tags = connection.execute(
                "SELECT tag, COUNT(*) AS n FROM tags GROUP BY tag ORDER BY n DESC LIMIT 100"
            ).fetchall()
            errors = connection.execute(
                "SELECT error, COUNT(*) AS n FROM runs WHERE error IS NOT NULL "
                "GROUP BY error ORDER BY n DESC LIMIT 20"
            ).fetchall()
            return s.RunFacets(
                agent=column("agent"),
                model=column("model"),
                user=column("user"),
                status=column("status"),
                environment=column("environment"),
                tool=dependency(FACET_KINDS["tool"]),
                mcp=dependency(FACET_KINDS["mcp"]),
                skill=dependency(FACET_KINDS["skill"]),
                tag=[s.FacetValue(value=str(r["tag"]), count=int(r["n"])) for r in tags],
                error=[
                    s.FacetValue(value=str(r["error"])[:120], count=int(r["n"])) for r in errors
                ],
            )

    def environments(self) -> list[s.EnvironmentView]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT environment, COUNT(*) AS n, MAX(started_at) AS last FROM runs "
                "GROUP BY environment ORDER BY n DESC"
            ).fetchall()
        return [
            s.EnvironmentView(
                name=str(row["environment"]),
                runs=int(row["n"]),
                last_run_at=_parse(row["last"]),
            )
            for row in rows
        ]

    def sessions(self, *, limit: int = 50) -> list[s.SessionView]:
        """Runs grouped into sessions (UI §4 navigation)."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT session_id, COUNT(*) AS n, MIN(started_at) AS first, "
                "MAX(COALESCE(ended_at, started_at)) AS last, SUM(cost) AS cost, "
                "SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failures, "
                "MAX(user) AS user FROM runs WHERE session_id IS NOT NULL "
                "GROUP BY session_id ORDER BY first DESC LIMIT ?",
                (limit,),
            ).fetchall()
            sessions: list[s.SessionView] = []
            for row in rows:
                agents = connection.execute(
                    "SELECT DISTINCT agent FROM runs WHERE session_id = ? AND agent IS NOT NULL",
                    (row["session_id"],),
                ).fetchall()
                started = _parse(row["first"])
                if started is None:
                    continue
                sessions.append(
                    s.SessionView(
                        id=str(row["session_id"]),
                        runs=int(row["n"]),
                        started_at=started,
                        ended_at=_parse(row["last"]),
                        agents=[str(a["agent"]) for a in agents],
                        user=row["user"],
                        cost=float(row["cost"] or 0.0),
                        failures=int(row["failures"] or 0),
                    )
                )
        return sessions

    def dependency_names(self, kinds: Sequence[str]) -> list[tuple[str, str, int]]:
        """``(name, version, runs)`` for the BUILD registry pages."""
        marks = ",".join("?" for _ in kinds)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT name, version, COUNT(DISTINCT run_id) AS n FROM facets "
                f"WHERE kind IN ({marks}) GROUP BY name, version ORDER BY n DESC",
                list(kinds),
            ).fetchall()
        return [(str(r["name"]), str(r["version"]), int(r["n"])) for r in rows]

    def dependency_versions(self, run_ids: Sequence[str]) -> dict[str, dict[str, str]]:
        """``run_id -> {"kind:name": version}``, the unit the change list diffs."""
        if not run_ids:
            return {}
        marks = ",".join("?" for _ in run_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT run_id, kind, name, version FROM facets WHERE run_id IN ({marks})",
                list(run_ids),
            ).fetchall()
        versions: dict[str, dict[str, str]] = {run_id: {} for run_id in run_ids}
        for row in rows:
            versions[str(row["run_id"])][f"{row['kind']}:{row['name']}"] = str(row["version"])
        return versions

    def registry(self, kinds: Sequence[str]) -> list[tuple[str, str, int, str, str]]:
        """``(name, version, runs, first_seen, last_seen)`` for the BUILD pages."""
        marks = ",".join("?" for _ in kinds)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT f.name, f.version, COUNT(*) AS n, MIN(r.started_at) AS first, "
                f"MAX(r.started_at) AS last FROM facets f JOIN runs r ON r.run_id = f.run_id "
                f"WHERE f.kind IN ({marks}) GROUP BY f.name, f.version "
                f"ORDER BY last DESC",
                list(kinds),
            ).fetchall()
        return [
            (str(r["name"]), str(r["version"]), int(r["n"]), str(r["first"]), str(r["last"]))
            for r in rows
        ]

    def runs_using(
        self, kinds: Sequence[str], name: str, *, limit: int = 100
    ) -> list[s.RunSummary]:
        """Every run that used one dependency, newest first."""
        marks = ",".join("?" for _ in kinds)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT r.summary, r.eval_score FROM runs r JOIN facets f ON f.run_id = r.run_id "
                f"WHERE f.kind IN ({marks}) AND f.name = ? "
                f"ORDER BY r.started_at DESC LIMIT ?",
                [*kinds, name, limit],
            ).fetchall()
        return [_summary_of(row) for row in rows]

    def used_with(self, kinds: Sequence[str], name: str, by: Sequence[str]) -> list[str]:
        """Which agents (or graphs) ran alongside this dependency."""
        marks = ",".join("?" for _ in kinds)
        by_marks = ",".join("?" for _ in by)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT DISTINCT owner.name FROM facets owner "
                f"WHERE owner.kind IN ({by_marks}) AND owner.run_id IN "
                f"(SELECT run_id FROM facets WHERE kind IN ({marks}) AND name = ?) "
                f"ORDER BY owner.name",
                [*by, *kinds, name],
            ).fetchall()
        return [str(row[0]) for row in rows]

    def dependencies_of(self, kinds: Sequence[str], name: str) -> list[s.DependencyView]:
        """Everything the runs of one agent used, latest version per dependency."""
        marks = ",".join("?" for _ in kinds)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT f.kind, f.name, f.version, MAX(r.started_at) AS last "
                f"FROM facets f JOIN runs r ON r.run_id = f.run_id WHERE f.run_id IN "
                f"(SELECT run_id FROM facets WHERE kind IN ({marks}) AND name = ?) "
                f"GROUP BY f.kind, f.name, f.version ORDER BY f.kind, f.name",
                [*kinds, name],
            ).fetchall()
        latest: dict[tuple[str, str], s.DependencyView] = {}
        for row in rows:
            key = (str(row["kind"]), str(row["name"]))
            latest[key] = s.DependencyView(kind=key[0], name=key[1], version=str(row["version"]))
        return list(latest.values())

    def evaluator_scores(self) -> list[tuple[str, float, bool, str]]:
        """``(evaluator, value, passed, scored_at)`` for every verdict recorded."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT evaluator, value, passed, scored_at FROM scores ORDER BY evaluator"
            ).fetchall()
        return [(str(r[0]), float(r[1]), bool(r[2]), str(r[3])) for r in rows]

    def scored_runs(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(DISTINCT subject_run_id) AS n FROM scores"
            ).fetchone()
        return int(row["n"]) if row else 0

    def cost_by(
        self, column: str, *, environment: str | None = None, limit: int = 50
    ) -> list[tuple[str, float, int, int]]:
        """``(key, total cost, runs, successes)`` grouped by one column (UI §33)."""
        allowed = {"agent", "model", "user", "environment", "project", "status", "session_id"}
        if column not in allowed:
            raise ValueError(f"cannot group cost by {column!r}")
        where = "WHERE environment = ?" if environment else ""
        args: list[Any] = [environment] if environment else []
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT COALESCE({column}, '—') AS key, SUM(cost) AS total, COUNT(*) AS runs, "
                f"SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS ok "
                f"FROM runs {where} GROUP BY key ORDER BY total DESC LIMIT ?",
                [*args, limit],
            ).fetchall()
        return [
            (str(r["key"]), float(r["total"] or 0.0), int(r["runs"]), int(r["ok"])) for r in rows
        ]

    def cost_by_day(
        self, *, environment: str | None = None, limit: int = 30
    ) -> list[tuple[str, float, int, int]]:
        where = "WHERE environment = ?" if environment else ""
        args: list[Any] = [environment] if environment else []
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT substr(started_at, 1, 10) AS key, SUM(cost) AS total, COUNT(*) AS runs, "
                f"SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS ok "
                f"FROM runs {where} GROUP BY key ORDER BY key DESC LIMIT ?",
                [*args, limit],
            ).fetchall()
        return [
            (str(r["key"]), float(r["total"] or 0.0), int(r["runs"]), int(r["ok"])) for r in rows
        ]

    def cost_categories(self, *, environment: str | None = None) -> dict[str, float]:
        """What the spend went on: the five categories §33 lists."""
        where = "WHERE environment = ?" if environment else ""
        args: list[Any] = [environment] if environment else []
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT SUM(cost_model) AS model, SUM(cost_tool) AS tool, "
                f"SUM(cost_embedding) AS embedding, SUM(cost_retrieval) AS retrieval, "
                f"SUM(cost_sandbox) AS sandbox, SUM(cost) AS total FROM runs {where}",
                args,
            ).fetchone()
        return {
            name: float(row[name] or 0.0)
            for name in ("model", "tool", "embedding", "retrieval", "sandbox", "total")
        }

    def cost_by_dependency(
        self, kinds: Sequence[str], *, limit: int = 50
    ) -> list[tuple[str, float, int, int]]:
        """Cost attributed to the runs that used each tool, skill or MCP server."""
        marks = ",".join("?" for _ in kinds)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT f.name AS key, SUM(r.cost) AS total, COUNT(*) AS runs, "
                f"SUM(CASE WHEN r.status = 'succeeded' THEN 1 ELSE 0 END) AS ok "
                f"FROM facets f JOIN runs r ON r.run_id = f.run_id "
                f"WHERE f.kind IN ({marks}) GROUP BY f.name ORDER BY total DESC LIMIT ?",
                [*kinds, limit],
            ).fetchall()
        return [
            (str(r["key"]), float(r["total"] or 0.0), int(r["runs"]), int(r["ok"])) for r in rows
        ]

    def stats(self, *, environment: str | None = None) -> dict[str, float]:
        """Aggregates for the overview (UI §6)."""
        clause = " WHERE environment = ?" if environment else ""
        args: list[Any] = [environment] if environment else []
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS runs, "
                f"SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS ok, "
                f"SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed, "
                f"AVG(duration_ms) AS latency, AVG(cost) AS cost, SUM(cost) AS spend "
                f"FROM runs{clause}",
                args,
            ).fetchone()
        runs = int(row["runs"] or 0)
        return {
            "runs": float(runs),
            "succeeded": float(row["ok"] or 0),
            "failed": float(row["failed"] or 0),
            "success_rate": (float(row["ok"] or 0) / runs * 100.0) if runs else 0.0,
            "avg_latency_ms": float(row["latency"] or 0.0),
            "avg_cost": float(row["cost"] or 0.0),
            "total_cost": float(row["spend"] or 0.0),
        }


def _summary_of(row: sqlite3.Row) -> s.RunSummary:
    """The stored summary, with the score column layered on top.

    A run is indexed when it is recorded; the verdict on it usually arrives
    later, from a different run, so the score lives in a column that is
    updated in place rather than in the frozen summary.
    """
    summary = s.RunSummary.model_validate_json(row["summary"])
    score = row["eval_score"]
    return summary if score is None else summary.model_copy(update={"eval_score": score})


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _recorded_score(manifest: RunManifest) -> float | None:
    """A score carried on the run itself.

    Most scores arrive from an evaluation run and are joined on afterwards;
    this only covers a run that recorded its own in metadata.
    """
    value = manifest.metadata.get("eval_score")
    return float(value) if isinstance(value, (int, float)) else None


def _search_text(summary: s.RunSummary, manifest: RunManifest) -> str:
    parts = [
        summary.id,
        summary.name,
        summary.status,
        summary.agent or "",
        summary.model or "",
        summary.user or "",
        summary.environment,
        summary.error or "",
        " ".join(summary.tags),
        " ".join(f"{d.kind}:{d.name}" for d in manifest.dependencies),
    ]
    return " ".join(p for p in parts if p).lower()


def _predicates(query: RunQuery) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []

    def equals(column: str, value: Any) -> None:
        if value is not None:
            where.append(f"{column} = ?")
            params.append(value)

    equals("project", query.project)
    equals("agent", query.agent)
    equals("model", query.model)
    equals("status", query.status)
    equals("user", query.user)
    equals("environment", query.environment)
    equals("session_id", query.session_id)
    if query.q:
        where.append("search_text LIKE ?")
        params.append(f"%{query.q.lower()}%")
    if query.error is True:
        where.append("error IS NOT NULL")
    elif query.error is False:
        where.append("error IS NULL")
    if query.tag:
        where.append("run_id IN (SELECT run_id FROM tags WHERE tag = ?)")
        params.append(query.tag)
    for filter_name, kinds in (
        ("tool", FACET_KINDS["tool"]),
        ("mcp", FACET_KINDS["mcp"]),
        ("skill", FACET_KINDS["skill"]),
    ):
        value = getattr(query, filter_name)
        if value:
            marks = ",".join("?" for _ in kinds)
            where.append(
                f"run_id IN (SELECT run_id FROM facets WHERE kind IN ({marks}) AND name = ?)"
            )
            params.extend([*kinds, value])
    for column, op, value in (
        ("started_at", ">=", _iso(query.since)),
        ("started_at", "<=", _iso(query.until)),
        ("cost", ">=", query.min_cost),
        ("cost", "<=", query.max_cost),
        ("duration_ms", ">=", query.min_latency_ms),
        ("duration_ms", "<=", query.max_latency_ms),
        ("eval_score", ">=", query.min_score),
        ("eval_score", "<=", query.max_score),
    ):
        if value is not None:
            where.append(f"{column} {op} ?")
            params.append(value)
    return where, params


def open_index(store: LocalStore | None = None) -> RunIndex:
    """Open (and refresh) the index for a store."""
    index = RunIndex(store)
    index.refresh()
    return index


__all__ = [
    "FACET_KINDS",
    "RunIndex",
    "RunQuery",
    "decode_cursor",
    "encode_cursor",
    "open_index",
]
