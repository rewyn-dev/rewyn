"""Local, file-based storage under ``.rewyn/`` (spec §44).

Layout::

    .rewyn/
    ├── config.json
    ├── runs/<run_id>/manifest.json
    ├── runs/<run_id>/events.jsonl
    ├── datasets/
    ├── checkpoints/
    └── memory/

Writes are append-only for events and atomic (write-then-rename) for
manifests so a crash never leaves a half-written manifest.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

from rewyn.core.event import Event
from rewyn.core.run import RunManifest
from rewyn.core.settings import get_settings
from rewyn.core.types import JSONObject, RewynError


class RunNotFoundError(RewynError):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"Run {run_id!r} was not found in local storage")
        self.run_id = run_id


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


class LocalStore:
    """Reads and writes runs, manifests and events on the local filesystem."""

    def __init__(self, home: Path | None = None) -> None:
        self.home = Path(home) if home is not None else get_settings().home

    # Layout ------------------------------------------------------------------
    @property
    def runs_dir(self) -> Path:
        return self.home / "runs"

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def manifest_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "manifest.json"

    def events_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "events.jsonl"

    def initialize(self) -> Path:
        """Create the directory layout and a config file. Idempotent."""
        for sub in ("runs", "datasets", "checkpoints", "memory"):
            (self.home / sub).mkdir(parents=True, exist_ok=True)
        config = self.home / "config.json"
        if not config.exists():
            atomic_write_text(config, json.dumps({"schema_version": "1"}, indent=2) + "\n")
        gitignore = self.home / ".gitignore"
        if not gitignore.exists():
            atomic_write_text(gitignore, "*\n")
        return self.home

    # Manifests ---------------------------------------------------------------
    def write_manifest(self, manifest: RunManifest) -> None:
        atomic_write_text(
            self.manifest_path(manifest.id), manifest.model_dump_json(indent=2) + "\n"
        )

    def read_manifest(self, run_id: str) -> RunManifest:
        """Read a manifest, upgrading it if it predates this build (spec §58)."""
        from rewyn.core.migrations import migrate

        path = self.manifest_path(run_id)
        if not path.exists():
            raise RunNotFoundError(run_id)
        record = json.loads(path.read_text(encoding="utf-8"))
        return RunManifest.model_validate(migrate("run", record))

    def exists(self, run_id: str) -> bool:
        return self.manifest_path(run_id).exists()

    # Events ------------------------------------------------------------------
    def append_events(self, run_id: str, events: Iterable[Event | JSONObject]) -> int:
        path = self.events_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with path.open("a", encoding="utf-8") as handle:
            for event in events:
                record = event.to_record() if isinstance(event, Event) else event
                handle.write(json.dumps(record, ensure_ascii=False, default=str))
                handle.write("\n")
                count += 1
            handle.flush()
        return count

    def iter_events(self, run_id: str) -> Iterator[Event]:
        path = self.events_path(run_id)
        if not path.exists():
            if not self.exists(run_id):
                raise RunNotFoundError(run_id)
            return
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if line:
                    yield Event.from_record(json.loads(line))

    def read_events(self, run_id: str) -> list[Event]:
        return list(self.iter_events(run_id))

    # Listing -----------------------------------------------------------------
    def list_runs(
        self, *, limit: int | None = None, project: str | None = None
    ) -> list[RunManifest]:
        if not self.runs_dir.exists():
            return []
        manifests: list[RunManifest] = []
        for entry in self.runs_dir.iterdir():
            if not (entry / "manifest.json").exists():
                continue
            try:
                manifest = self.read_manifest(entry.name)
            except (ValueError, OSError):
                continue
            if project is not None and manifest.project != project:
                continue
            manifests.append(manifest)
        manifests.sort(key=lambda m: m.started_at, reverse=True)
        return manifests[:limit] if limit else manifests

    def delete_run(self, run_id: str) -> None:
        directory = self.run_dir(run_id)
        if not directory.exists():
            raise RunNotFoundError(run_id)
        shutil.rmtree(directory)

    # Misc --------------------------------------------------------------------
    def resolve_run_id(self, prefix: str) -> str:
        """Resolve a full or unique-prefix run id (also accepts ``latest``)."""
        if prefix == "latest":
            runs = self.list_runs(limit=1)
            if not runs:
                raise RunNotFoundError(prefix)
            return runs[0].id
        if self.exists(prefix):
            return prefix
        matches = [m.id for m in self.list_runs() if m.id.startswith(prefix)]
        if len(matches) == 1:
            return matches[0]
        raise RunNotFoundError(prefix)
