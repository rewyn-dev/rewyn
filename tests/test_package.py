from __future__ import annotations

import subprocess
import sys

import pytest

import rewyn


def test_version_is_exposed() -> None:
    assert rewyn.__version__


def test_unknown_attribute_raises() -> None:
    with pytest.raises(AttributeError, match="does_not_exist"):
        rewyn.does_not_exist  # noqa: B018


def test_importing_models_does_not_import_agents() -> None:
    code = (
        "import sys, rewyn.models; "
        "assert 'rewyn.agents' not in sys.modules, 'agents imported eagerly'; "
        "assert 'rewyn.rag' not in sys.modules, 'rag imported eagerly'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
