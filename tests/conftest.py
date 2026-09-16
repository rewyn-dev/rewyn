"""Shared test fixtures.

Every test runs against an isolated ``REWYN_HOME`` so no test can touch the
developer's real ``.rewyn/`` directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def rewyn_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".rewyn"
    monkeypatch.setenv("REWYN_HOME", str(home))
    return home
