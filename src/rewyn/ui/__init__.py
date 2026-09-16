"""The Rewyn console: the local UI and the shared API contract (UI spec §44).

``rewyn ui`` serves a developer's own ``.rewyn/`` on localhost with no
account and no cloud, which is what makes the open-source workflow complete
(UI §44, SDK §44). The models in :mod:`rewyn.ui.schemas` and the read model
in :mod:`rewyn.ui.projections` are also what ``rewyn-cloud`` serves, so
one frontend works against either surface.

Imports are lazy: pulling in the console must not pull in a web framework.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from rewyn.ui.index import RunIndex
    from rewyn.ui.schemas import CONSOLE_API_VERSION, Capabilities, RunPage, RunSummary
    from rewyn.ui.server import build_app, serve

__all__ = [
    "CONSOLE_API_VERSION",
    "Capabilities",
    "RunIndex",
    "RunPage",
    "RunSummary",
    "build_app",
    "serve",
]

_EXPORTS = {
    "CONSOLE_API_VERSION": "rewyn.ui.schemas",
    "Capabilities": "rewyn.ui.schemas",
    "RunPage": "rewyn.ui.schemas",
    "RunSummary": "rewyn.ui.schemas",
    "RunIndex": "rewyn.ui.index",
    "build_app": "rewyn.ui.server",
    "serve": "rewyn.ui.server",
}


_MODULES = frozenset(
    {
        "aggregates",
        "collaboration",
        "explain",
        "incidents",
        "index",
        "intelligence",
        "lifecycle",
        "live",
        "projections",
        "registries",
        "schemas",
        "server",
        "workspaces",
    }
)
"""The subpackage's own modules.

``from rewyn.ui import incidents`` works through the import system either
way; naming them here means ``hasattr(rewyn.ui, "incidents")`` is true
before anything has imported it, so the modular-import rule holds for
introspection as well as for imports.
"""


def __getattr__(name: str) -> Any:
    import importlib

    if name in _MODULES:
        return importlib.import_module(f"{__name__}.{name}")
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module_name), name)


def __dir__() -> list[str]:
    return sorted({*__all__, *_MODULES})
