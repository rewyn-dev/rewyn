"""Portable run manifest export/import (spec §43).

``rewyn export run_123`` produces::

    run_123/
    ├── manifest.json
    ├── events.jsonl
    ├── context/
    ├── tools/
    ├── prompts/
    ├── outputs/
    └── metadata.json

The bundle is self-describing and importable into another installation.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from rewyn import __version__
from rewyn.core.event import EVENT_SCHEMA_VERSION, Event, EventType
from rewyn.core.run import RUN_SCHEMA_VERSION, RunManifest
from rewyn.core.types import RewynError, utcnow
from rewyn.storage.local import LocalStore, atomic_write_text

BUNDLE_SCHEMA_VERSION = "1"

_PROMPT_EVENTS = {EventType.MODEL_CALLED}
_OUTPUT_EVENTS = {EventType.MODEL_RESPONSE, EventType.OUTPUT_VALIDATED}
_TOOL_EVENTS = {EventType.TOOL_CALLED, EventType.TOOL_RETURNED, EventType.TOOL_DENIED}
_CONTEXT_EVENTS = {
    EventType.CONTEXT_RETRIEVED,
    EventType.CONTEXT_ASSEMBLED,
    EventType.MEMORY_READ,
    EventType.MEMORY_WRITE,
    EventType.RETRIEVAL_QUERIED,
    EventType.RETRIEVAL_RERANKED,
    EventType.SKILL_LOADED,
}


class BundleError(RewynError):
    pass


def _dump(path: Path, data: object) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")


def export_run(
    run_id: str,
    dest: Path | str | None = None,
    *,
    store: LocalStore | None = None,
    archive: bool = False,
) -> Path:
    """Write a portable bundle for ``run_id``. Returns the bundle directory (or zip)."""
    store = store or LocalStore()
    run_id = store.resolve_run_id(run_id)
    manifest = store.read_manifest(run_id)
    events = store.read_events(run_id)
    base = Path(dest) if dest is not None else Path.cwd()
    bundle = base / run_id
    if bundle.exists():
        shutil.rmtree(bundle)
    for sub in ("context", "tools", "prompts", "outputs"):
        (bundle / sub).mkdir(parents=True, exist_ok=True)

    _dump(bundle / "manifest.json", manifest.model_dump(mode="json"))
    with (bundle / "events.jsonl").open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event.to_record(), ensure_ascii=False, default=str) + "\n")

    for event in events:
        record = event.to_record()
        name = f"{event.seq:06d}_{event.type.value.lower()}.json"
        if event.type in _PROMPT_EVENTS:
            _dump(bundle / "prompts" / name, record)
        elif event.type in _OUTPUT_EVENTS:
            _dump(bundle / "outputs" / name, record)
        elif event.type in _TOOL_EVENTS:
            _dump(bundle / "tools" / name, record)
        elif event.type in _CONTEXT_EVENTS:
            _dump(bundle / "context" / name, record)
    if manifest.output is not None:
        _dump(bundle / "outputs" / "final.json", {"output": manifest.output})

    _dump(
        bundle / "metadata.json",
        {
            "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
            "run_schema_version": RUN_SCHEMA_VERSION,
            "event_schema_version": EVENT_SCHEMA_VERSION,
            "sdk_version": __version__,
            "exported_at": utcnow().isoformat(),
            "run_id": run_id,
            "event_count": len(events),
        },
    )
    if not archive:
        return bundle
    zip_path = base / f"{run_id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(base))
    shutil.rmtree(bundle)
    return zip_path


def import_run(
    source: Path | str, *, store: LocalStore | None = None, overwrite: bool = False
) -> str:
    """Import a bundle directory or zip file. Returns the run id."""
    store = store or LocalStore()
    path = Path(source)
    if not path.exists():
        raise BundleError(f"bundle {path} does not exist")
    if path.is_file() and path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            roots = {n.split("/", 1)[0] for n in names if "/" in n}
            if len(roots) != 1:
                raise BundleError("zip bundle must contain exactly one run directory")
            root = roots.pop()
            manifest = RunManifest.model_validate_json(zf.read(f"{root}/manifest.json"))
            lines = zf.read(f"{root}/events.jsonl").decode("utf-8").splitlines()
    else:
        manifest_path = path / "manifest.json"
        if not manifest_path.exists():
            raise BundleError(f"{path} is not a run bundle (manifest.json missing)")
        manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        events_path = path / "events.jsonl"
        lines = events_path.read_text(encoding="utf-8").splitlines() if events_path.exists() else []
    if store.exists(manifest.id):
        if not overwrite:
            raise BundleError(f"run {manifest.id} already exists; use overwrite=True to replace")
        store.delete_run(manifest.id)
    store.write_manifest(manifest)
    events = [Event.from_record(json.loads(line)) for line in lines if line.strip()]
    if events:
        store.append_events(manifest.id, events)
    return manifest.id
