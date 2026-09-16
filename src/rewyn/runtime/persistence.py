"""File-backed checkpoint persistence under ``.rewyn/checkpoints/``."""

from __future__ import annotations

from pathlib import Path

from rewyn.core.settings import get_settings
from rewyn.runtime.checkpoint import Checkpoint
from rewyn.security.redaction import default_redactor
from rewyn.storage.local import atomic_write_text


class FileCheckpointStore:
    name = "file"

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory) if directory else get_settings().checkpoints_dir

    def _path(self, run_id: str, checkpoint_id: str) -> Path:
        return self.directory / run_id / f"{checkpoint_id}.json"

    async def save(self, checkpoint: Checkpoint) -> None:
        data = default_redactor().redact(checkpoint.model_dump(mode="json"))
        redacted = Checkpoint.model_validate(data)
        atomic_write_text(
            self._path(checkpoint.run_id, checkpoint.id), redacted.model_dump_json(indent=2)
        )
        index = self.directory / "index.json"
        mapping = _read_index(index)
        mapping[checkpoint.id] = checkpoint.run_id
        atomic_write_text(index, _dump_index(mapping))

    async def load(self, checkpoint_id: str) -> Checkpoint | None:
        mapping = _read_index(self.directory / "index.json")
        run_id = mapping.get(checkpoint_id)
        if run_id is None:
            return None
        path = self._path(run_id, checkpoint_id)
        if not path.exists():
            return None
        return Checkpoint.model_validate_json(path.read_text(encoding="utf-8"))

    async def list_for_run(self, run_id: str) -> list[Checkpoint]:
        folder = self.directory / run_id
        if not folder.is_dir():
            return []
        items = [
            Checkpoint.model_validate_json(p.read_text(encoding="utf-8"))
            for p in folder.glob("*.json")
        ]
        return sorted(items, key=lambda c: c.sequence)


def _read_index(path: Path) -> dict[str, str]:
    import json

    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _dump_index(mapping: dict[str, str]) -> str:
    import json

    return json.dumps(mapping, indent=2, sort_keys=True) + "\n"
