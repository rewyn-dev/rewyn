"""Local and remote run storage (spec §44, §45)."""

from rewyn.storage.local import LocalStore, RunNotFoundError, atomic_write_text

__all__ = ["LocalStore", "RunNotFoundError", "atomic_write_text", "remote"]


def __getattr__(name: str) -> object:
    if name == "remote":
        import importlib

        return importlib.import_module("rewyn.storage.remote")
    raise AttributeError(f"module 'rewyn.storage' has no attribute {name!r}")
