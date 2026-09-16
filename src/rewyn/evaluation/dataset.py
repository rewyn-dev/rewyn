"""Evaluation datasets (spec §30).

The core product loop is::

    Production Run -> Save as Example -> Dataset -> Regression test

So any recorded run can become a dataset item without re-typing anything:
its input becomes the case input and, by default, its output becomes the
expected output -- a golden example captured from real traffic.

Datasets are versioned and fingerprinted like every other Rewyn object, so
a regression report can always say exactly which dataset it ran against.
They live in ``.rewyn/datasets/<name>.json`` and need no account or server.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.schema import fingerprint
from rewyn.core.settings import get_settings
from rewyn.core.types import JSONObject, RewynError, new_id, utcnow

DATASET_SCHEMA_VERSION = "1"


class DatasetError(RewynError):
    """A dataset could not be read, written or resolved."""


class DatasetItem(BaseModel):
    """One evaluation case."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("item"))
    input: Any = None
    expected: Any = None
    metadata: JSONObject = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    source_run_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)

    def fingerprint(self) -> str:
        return fingerprint({"input": self.input, "expected": self.expected, "tags": self.tags})

    @property
    def input_text(self) -> str:
        value = self.input
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class Dataset(BaseModel):
    """A named, versioned collection of evaluation cases."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str = "1"
    description: str = ""
    schema_version: str = DATASET_SCHEMA_VERSION
    items: list[DatasetItem] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    metadata: JSONObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    # Identity ----------------------------------------------------------------
    def fingerprint(self) -> str:
        return fingerprint(
            {
                "name": self.name,
                "version": self.version,
                "items": [i.fingerprint() for i in self.items],
            }
        )

    @property
    def dependency(self) -> Any:
        from rewyn.core.run import DependencyRef

        return DependencyRef(
            kind="dataset",
            name=self.name,
            version=self.version,
            fingerprint=self.fingerprint(),
            metadata={"items": len(self.items)},
        )

    # Collection behaviour ----------------------------------------------------
    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[DatasetItem]:  # type: ignore[override]
        return iter(self.items)

    def __getitem__(self, index: int) -> DatasetItem:
        return self.items[index]

    def get(self, item_id: str) -> DatasetItem | None:
        return next((i for i in self.items if i.id == item_id), None)

    def filter(self, *, tags: Sequence[str] = ()) -> Dataset:
        """A copy holding only the items carrying every tag given."""
        wanted = set(tags)
        if not wanted:
            return self.model_copy(update={"items": list(self.items)})
        kept = [i for i in self.items if wanted.issubset(set(i.tags))]
        return self.model_copy(update={"items": kept})

    # Building ----------------------------------------------------------------
    def add(
        self,
        input: Any,
        expected: Any = None,
        *,
        tags: Sequence[str] = (),
        source_run_id: str | None = None,
        **metadata: Any,
    ) -> DatasetItem:
        """Add one case."""
        item = DatasetItem(
            input=input,
            expected=expected,
            tags=list(tags),
            source_run_id=source_run_id,
            metadata=metadata,
        )
        self.items.append(item)
        self.updated_at = utcnow()
        return item

    def add_run(
        self,
        run: Any,
        *,
        expected: Any = None,
        tags: Sequence[str] = (),
        store: Any = None,
        **metadata: Any,
    ) -> DatasetItem:
        """Turn a recorded run into a case ("save as example").

        ``run`` may be a run id, a :class:`~rewyn.core.run.Run`, or an
        already loaded :class:`~rewyn.replay.recorder.RecordedRun`. When
        ``expected`` is omitted the run's own output becomes the expectation,
        which is what makes a production run a golden example.
        """
        from rewyn.replay.recorder import RecordedRun

        if isinstance(run, str):
            recorded = RecordedRun.load(run, store=store)
        elif isinstance(run, RecordedRun):
            recorded = run
        elif hasattr(run, "manifest") and hasattr(run, "events"):
            recorded = RecordedRun.from_run(run)
        else:
            raise DatasetError(f"cannot build a dataset item from {type(run).__name__}")
        return self.add(
            recorded.input,
            recorded.output if expected is None else expected,
            tags=tags,
            source_run_id=recorded.id,
            **{
                "captured_cost": recorded.manifest.cost.total,
                "captured_tools": [c.name for c in recorded.tool_calls],
                **metadata,
            },
        )

    def extend(self, items: Iterable[DatasetItem]) -> Dataset:
        self.items.extend(items)
        self.updated_at = utcnow()
        return self

    def bump(self, version: str | None = None) -> Dataset:
        """Set a new version (default: increment the integer version)."""
        if version is None:
            try:
                version = str(int(self.version) + 1)
            except ValueError:
                raise DatasetError(f"cannot auto-increment version {self.version!r}") from None
        self.version = version
        self.updated_at = utcnow()
        return self

    # Storage -----------------------------------------------------------------
    @staticmethod
    def directory(home: Path | None = None) -> Path:
        return (home / "datasets") if home is not None else get_settings().datasets_dir

    @staticmethod
    def path_for(name: str, *, home: Path | None = None) -> Path:
        return Dataset.directory(home) / f"{name}.json"

    def save(self, *, home: Path | None = None) -> Path:
        """Write the dataset to ``.rewyn/datasets/<name>.json``."""
        from rewyn.security.redaction import default_redactor
        from rewyn.storage.local import atomic_write_text

        self.updated_at = utcnow()
        payload = default_redactor().redact(self.model_dump(mode="json"))
        path = Dataset.path_for(self.name, home=home)
        atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return path

    @classmethod
    def load(cls, name: str | Path, *, home: Path | None = None) -> Dataset:
        """Read a dataset by name or explicit path."""
        from rewyn.core.migrations import migrate

        named = Path(name)
        path = named if named.suffix == ".json" else Dataset.path_for(str(name), home=home)
        if not path.exists():
            raise DatasetError(f"dataset {str(name)!r} not found at {path}")
        record = json.loads(path.read_text(encoding="utf-8"))
        return cls.model_validate(migrate("dataset", record))

    @classmethod
    def load_or_create(cls, name: str, *, home: Path | None = None, **fields: Any) -> Dataset:
        try:
            return cls.load(name, home=home)
        except DatasetError:
            return cls(name=name, **fields)

    @classmethod
    def from_runs(
        cls,
        name: str,
        run_ids: Sequence[str],
        *,
        tags: Sequence[str] = (),
        store: Any = None,
        **fields: Any,
    ) -> Dataset:
        """Build a dataset from recorded runs."""
        dataset = cls(name=name, **fields)
        for run_id in run_ids:
            dataset.add_run(run_id, tags=tags, store=store)
        return dataset

    @classmethod
    def from_jsonl(cls, name: str, path: Path | str, **fields: Any) -> Dataset:
        """Build a dataset from a JSONL file of ``{"input":…, "expected":…}`` rows."""
        dataset = cls(name=name, **fields)
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            dataset.add(
                row.get("input"),
                row.get("expected"),
                tags=row.get("tags", ()),
                **row.get("metadata", {}),
            )
        return dataset

    def to_jsonl(self, path: Path | str) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(
                {
                    "input": i.input,
                    "expected": i.expected,
                    "tags": i.tags,
                    "metadata": i.metadata,
                },
                ensure_ascii=False,
                default=str,
            )
            for i in self.items
        ]
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return target


def list_datasets(*, home: Path | None = None) -> list[Dataset]:
    """Every dataset in local storage, newest first."""
    directory = Dataset.directory(home)
    if not directory.exists():
        return []
    datasets: list[Dataset] = []
    for path in sorted(directory.glob("*.json")):
        try:
            datasets.append(Dataset.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    datasets.sort(key=lambda d: d.updated_at, reverse=True)
    return datasets
