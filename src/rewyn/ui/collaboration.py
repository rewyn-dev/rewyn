"""Comments, saved views, incident ownership and promotions (UI §36, §43, §45).

Everything else the console shows is *derived*: delete the cache and it comes
back from the runs. This module holds the one kind of data that cannot be
re-derived, because a person wrote it -- a note on a run, a filter someone
kept, the name of whoever took an incident. So it gets its own durable store
next to the index rather than a table inside it: :class:`~rewyn.ui.index.RunIndex`
drops and rebuilds its schema whenever the projection version moves, and that
must never take a colleague's comment -- or a release promotion -- with it.

The cloud stores the same three things in PostgreSQL, scoped to a project and
written only by principals with write permission (UI §54, §55). Both surfaces
serve the same models, so the comment thread on a run is one component.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from rewyn.core.types import new_id, utcnow
from rewyn.storage.local import LocalStore
from rewyn.ui import schemas as s

SCHEMA = """
CREATE TABLE IF NOT EXISTS comments (
    id          TEXT PRIMARY KEY,
    subject     TEXT NOT NULL,
    author      TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    resolved    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS comments_subject ON comments (subject, created_at);

CREATE TABLE IF NOT EXISTS saved_views (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    screen      TEXT NOT NULL,
    query       TEXT NOT NULL DEFAULT '',
    author      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    shared      INTEGER NOT NULL DEFAULT 1,
    description TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS saved_views_screen ON saved_views (screen, name);

CREATE TABLE IF NOT EXISTS promotions (
    release_id  TEXT PRIMARY KEY,
    application TEXT NOT NULL,
    version     TEXT NOT NULL,
    environment TEXT NOT NULL DEFAULT 'production',
    by          TEXT NOT NULL,
    at          TEXT NOT NULL,
    report_id   TEXT,
    note        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS incident_state (
    incident_id TEXT PRIMARY KEY,
    status      TEXT NOT NULL DEFAULT 'open',
    assignee    TEXT,
    note        TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL,
    history     TEXT NOT NULL DEFAULT '[]'
);
"""

# What a comment can be attached to. A subject the console cannot open is
# refused rather than stored, so no thread can be orphaned by a typo.
SUBJECT_KINDS = frozenset({"run", "incident", "dataset", "agent", "release", "view"})


def local_author() -> str:
    """Who is writing, on a surface with no accounts (UI §44)."""
    import getpass

    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - no login name in some sandboxes
        return "local"


def parse_subject(subject: str) -> tuple[str, str]:
    """``run:abc`` → ``("run", "abc")``. Raises ``ValueError`` on anything else."""
    kind, _, identifier = subject.partition(":")
    if kind not in SUBJECT_KINDS or not identifier:
        raise ValueError(
            f"{subject!r} is not something the console can hold a discussion about; "
            f"use one of {', '.join(sorted(SUBJECT_KINDS))} followed by an id"
        )
    return kind, identifier


def subject_href(subject: str) -> str:
    kind, identifier = parse_subject(subject)
    return {
        "run": f"/runs/{identifier}",
        "incident": f"/incidents/{identifier}",
        "dataset": f"/datasets/{identifier}",
        "agent": f"/agents/{identifier}",
        "release": f"/releases?agent={identifier}",
        "view": f"/views/{identifier}",
    }[kind]


class CollabStore:
    """The durable half of the console: what people wrote, not what ran."""

    def __init__(self, store: LocalStore | None = None, *, path: Path | None = None) -> None:
        self.store = store or LocalStore()
        self.path = path or (self.store.home / "ui" / "collaboration.db")
        self._ensure_schema()

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

    # Comments -----------------------------------------------------------------
    def comments(self, subject: str | None = None, *, limit: int = 200) -> list[s.IncidentComment]:
        sql = "SELECT * FROM comments"
        args: list[object] = []
        if subject is not None:
            parse_subject(subject)
            sql += " WHERE subject = ?"
            args.append(subject)
        sql += " ORDER BY created_at ASC LIMIT ?"
        args.append(limit)
        with self._connect() as connection:
            return [_comment(row) for row in connection.execute(sql, args)]

    def add_comment(self, request: s.CommentRequest, *, author: str) -> s.IncidentComment:
        parse_subject(request.subject)
        body = request.body.strip()
        if not body:
            raise ValueError("a comment needs something in it")
        comment = s.IncidentComment(
            id=new_id("cmt"),
            subject=request.subject,
            author=request.author or author,
            body=body,
            created_at=utcnow(),
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO comments (id, subject, author, body, created_at, resolved) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (
                    comment.id,
                    comment.subject,
                    comment.author,
                    comment.body,
                    comment.created_at.isoformat(),
                ),
            )
        return comment

    def resolve_comment(self, comment_id: str, *, resolved: bool = True) -> s.IncidentComment:
        with self._connect() as connection:
            connection.execute(
                "UPDATE comments SET resolved = ? WHERE id = ?", (int(resolved), comment_id)
            )
            row = connection.execute(
                "SELECT * FROM comments WHERE id = ?", (comment_id,)
            ).fetchone()
        if row is None:
            raise KeyError(comment_id)
        return _comment(row)

    def comment_counts(self) -> dict[str, int]:
        with self._connect() as connection:
            return {
                str(row["subject"]): int(row["n"])
                for row in connection.execute(
                    "SELECT subject, COUNT(*) AS n FROM comments GROUP BY subject"
                )
            }

    # Saved views --------------------------------------------------------------
    def views(self, screen: str | None = None) -> list[s.SavedView]:
        sql = "SELECT * FROM saved_views"
        args: list[object] = []
        if screen is not None:
            sql += " WHERE screen = ?"
            args.append(screen)
        sql += " ORDER BY name"
        with self._connect() as connection:
            return [_view(row) for row in connection.execute(sql, args)]

    def add_view(self, request: s.SavedViewRequest, *, author: str) -> s.SavedView:
        name = request.name.strip()
        screen = request.screen.strip().strip("/")
        if not name or not screen:
            raise ValueError("a saved view needs a name and the screen it belongs to")
        view = s.SavedView(
            id=new_id("view"),
            name=name,
            screen=screen,
            query=request.query,
            author=request.author or author,
            created_at=utcnow(),
            shared=request.shared,
            description=request.description,
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO saved_views "
                "(id, name, screen, query, author, created_at, shared, description) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    view.id,
                    view.name,
                    view.screen,
                    view.query,
                    view.author,
                    view.created_at.isoformat(),
                    int(view.shared),
                    view.description,
                ),
            )
        return view

    def delete_view(self, view_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM saved_views WHERE id = ?", (view_id,))
            return cursor.rowcount > 0

    # Promotions ---------------------------------------------------------------
    def promotions(self) -> dict[str, s.PromotionRecord]:
        with self._connect() as connection:
            return {
                str(row["release_id"]): _promotion(row)
                for row in connection.execute("SELECT * FROM promotions")
            }

    def promotion(self, release_id: str) -> s.PromotionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM promotions WHERE release_id = ?", (release_id,)
            ).fetchone()
        return _promotion(row) if row is not None else None

    def record_promotion(self, record: s.PromotionRecord) -> s.PromotionRecord:
        """Keep an authorized promotion. Re-promoting replaces the decision."""
        with self._connect() as connection:
            connection.execute(
                "REPLACE INTO promotions "
                "(release_id, application, version, environment, by, at, report_id, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.release_id,
                    record.application,
                    record.version,
                    record.environment,
                    record.by,
                    record.at.isoformat(),
                    record.report_id,
                    record.note,
                ),
            )
        return record

    # Incident ownership -------------------------------------------------------
    def incident_state(self, incident_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM incident_state WHERE incident_id = ?", (incident_id,)
            ).fetchone()
        if row is None:
            return {"status": "open", "assignee": None, "note": "", "history": []}
        return {
            "status": row["status"],
            "assignee": row["assignee"],
            "note": row["note"],
            "updated_at": row["updated_at"],
            "history": json.loads(row["history"]),
        }

    def update_incident(
        self, incident_id: str, update: s.IncidentUpdate, *, author: str
    ) -> dict[str, Any]:
        current = self.incident_state(incident_id)
        history = list(current.get("history") or [])
        moment = utcnow()
        if update.status is not None and update.status != current["status"]:
            history.append({"at": moment.isoformat(), "status": update.status, "by": author})
        state: dict[str, Any] = {
            "status": update.status or current["status"],
            "assignee": (update.assignee if update.assignee is not None else current["assignee"]),
            "note": update.note if update.note is not None else current["note"],
        }
        with self._connect() as connection:
            connection.execute(
                "REPLACE INTO incident_state "
                "(incident_id, status, assignee, note, updated_at, history) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    incident_id,
                    state["status"],
                    state["assignee"],
                    state["note"],
                    moment.isoformat(),
                    json.dumps(history),
                ),
            )
        return {**state, "updated_at": moment.isoformat(), "history": history}

    def incident_states(self) -> dict[str, dict[str, Any]]:
        with self._connect() as connection:
            return {
                str(row["incident_id"]): {
                    "status": row["status"],
                    "assignee": row["assignee"],
                    "note": row["note"],
                    "updated_at": row["updated_at"],
                    "history": json.loads(row["history"]),
                }
                for row in connection.execute("SELECT * FROM incident_state")
            }


def _promotion(row: sqlite3.Row) -> s.PromotionRecord:
    return s.PromotionRecord(
        release_id=row["release_id"],
        application=row["application"],
        version=row["version"],
        environment=row["environment"],
        by=row["by"],
        at=datetime.fromisoformat(row["at"]),
        report_id=row["report_id"],
        note=row["note"],
    )


def _comment(row: sqlite3.Row) -> s.IncidentComment:
    return s.IncidentComment(
        id=row["id"],
        subject=row["subject"],
        author=row["author"],
        body=row["body"],
        created_at=datetime.fromisoformat(row["created_at"]),
        resolved=bool(row["resolved"]),
    )


def _view(row: sqlite3.Row) -> s.SavedView:
    return s.SavedView(
        id=row["id"],
        name=row["name"],
        screen=row["screen"],
        query=row["query"],
        author=row["author"],
        created_at=datetime.fromisoformat(row["created_at"]),
        shared=bool(row["shared"]),
        description=row["description"],
    )


def thread(comments: Sequence[s.IncidentComment], subject: str) -> list[s.IncidentComment]:
    """The comments on one subject, oldest first."""
    return [c for c in comments if c.subject == subject]
