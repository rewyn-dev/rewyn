"""The frontend's types are the backend's schemas (UI spec §52).

The console ships a hand-written TypeScript mirror of the Pydantic models.
That is only safe if something fails when they diverge, so this test is that
something: every console model must have an interface of the same name, with
a property for every field. A renamed field breaks the build here instead of
rendering as `undefined` in a panel.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import BaseModel

from rewyn.ui import schemas

TYPES = Path(__file__).resolve().parents[2] / "web" / "api" / "types.ts"

# Models the frontend does not render (they only exist inside the server).
NOT_IN_THE_UI = {"ConsoleModel"}


def console_models() -> list[type[BaseModel]]:
    return [
        value
        for name, value in vars(schemas).items()
        if isinstance(value, type)
        and issubclass(value, BaseModel)
        and value is not BaseModel
        and name not in NOT_IN_THE_UI
        and value.__module__ == schemas.__name__
    ]


def interfaces(source: str) -> dict[str, str]:
    """Interface name -> its body, with anything it extends folded in."""
    bodies: dict[str, str] = {}
    parents: dict[str, str] = {}
    pattern = r"export interface (\w+)(?: extends (\w+))? \{(.*?)\n\}"
    for match in re.finditer(pattern, source, re.S):
        bodies[match.group(1)] = match.group(3)
        if match.group(2):
            parents[match.group(1)] = match.group(2)
    for child, parent in parents.items():
        bodies[child] += bodies.get(parent, "")
    return bodies


@pytest.fixture(scope="module")
def declared() -> dict[str, str]:
    assert TYPES.exists(), f"the console types are missing: {TYPES}"
    return interfaces(TYPES.read_text(encoding="utf-8"))


@pytest.mark.parametrize("model", console_models(), ids=lambda m: m.__name__)
def test_every_console_model_has_a_typescript_interface(
    model: type[BaseModel], declared: dict[str, str]
) -> None:
    assert model.__name__ in declared, (
        f"web/api/types.ts has no interface for {model.__name__}; "
        "the console cannot render a model it cannot type"
    )


@pytest.mark.parametrize("model", console_models(), ids=lambda m: m.__name__)
def test_every_field_is_declared(model: type[BaseModel], declared: dict[str, str]) -> None:
    body = declared.get(model.__name__)
    if body is None:
        pytest.skip("covered by the interface test")
    missing = [
        field
        for field in model.model_fields
        if not re.search(rf"^\s*{re.escape(field)}\??:", body, re.M)
    ]
    assert not missing, f"{model.__name__} fields missing from types.ts: {missing}"


def test_the_prefix_the_client_calls_is_the_prefix_the_server_serves(
    declared: dict[str, str],
) -> None:
    client = (TYPES.parent / "client.ts").read_text(encoding="utf-8")
    assert f'export const BASE = "{schemas.CONSOLE_PREFIX}";' in client
