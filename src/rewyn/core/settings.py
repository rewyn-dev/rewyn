"""Process-level settings, resolved from environment variables.

Rewyn is local-first: the only required setting is where local state
lives, and it defaults to ``./.rewyn``.

Environment variables:

- ``REWYN_HOME``: directory for local state (default ``./.rewyn``).
- ``REWYN_RECORDING``: ``0``/``off``/``false`` disables local recording.
- ``REWYN_REDACTION``: ``0``/``off``/``false`` disables secret redaction.
  Redaction stays on by default; disabling it is an explicit opt-out.
- ``REWYN_PROJECT``: project name attached to every run manifest.
- ``REWYN_ENV``: environment name attached to every run (default
  ``development``). The console filters and groups by it, so setting it once
  per deployment is what makes "production" mean production (UI §42).
- ``REWYN_SAMPLE_RATE``: fraction of runs to record, 0.0 to 1.0 (default
  1.0). Sampling is per run, so a recorded run is always complete. Failed
  runs are always kept.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_FALSE_VALUES = frozenset({"0", "off", "false", "no"})


def _env_rate(name: str, default: float) -> float:
    """A 0..1 fraction from the environment. An unparseable value keeps the default."""
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return min(1.0, max(0.0, float(value)))
    except ValueError:
        return default


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in _FALSE_VALUES


@dataclass(frozen=True, slots=True)
class Settings:
    home: Path = field(default_factory=lambda: Path(".rewyn"))
    recording: bool = True
    redaction: bool = True
    project: str = "default"
    environment: str = "development"
    sample_rate: float = 1.0

    @property
    def runs_dir(self) -> Path:
        return self.home / "runs"

    @property
    def datasets_dir(self) -> Path:
        return self.home / "datasets"

    @property
    def checkpoints_dir(self) -> Path:
        return self.home / "checkpoints"

    @property
    def memory_dir(self) -> Path:
        return self.home / "memory"


def get_settings() -> Settings:
    """Resolve settings from the environment. Cheap; never cached."""
    home = os.environ.get("REWYN_HOME")
    return Settings(
        home=Path(home).expanduser() if home else Path(".rewyn"),
        recording=_env_flag("REWYN_RECORDING", True),
        redaction=_env_flag("REWYN_REDACTION", True),
        project=os.environ.get("REWYN_PROJECT", "default"),
        environment=os.environ.get("REWYN_ENV", "").strip() or "development",
        sample_rate=_env_rate("REWYN_SAMPLE_RATE", 1.0),
    )
