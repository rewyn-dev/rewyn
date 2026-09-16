"""Schema migration utilities (spec §58).

Recorded runs outlive the code that produced them. A run captured last
quarter must still load, replay and diff after the event schema moves on, or
the recording was never durable in the first place.

Every persisted object declares a ``schema_version``. Migrations are
registered per schema and per version, and applied on read, in order, until
the payload reaches the current version. Reads go through
:func:`migrate`, so old data is upgraded in memory without rewriting files
on disk.

Registering one looks like this::

    @migration("event", "1")
    def add_severity(record: dict) -> dict:
        record["severity"] = "info"
        return record

The function takes a version-1 record and returns a version-2 one. Nothing
is registered today because every schema is still at version 1; the
machinery exists so the first change is a migration rather than a breakage.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from rewyn.core.types import JSONObject, RewynError

Schema = Literal["run", "event", "skill", "manifest", "dataset", "report", "bundle"]

Migrator = Callable[[JSONObject], JSONObject]

_MIGRATIONS: dict[tuple[str, str], tuple[str, Migrator]] = {}
"""(schema, from_version) -> (to_version, function)."""


class MigrationError(RewynError):
    """A record could not be brought up to the current schema version."""


def current_version(schema: Schema) -> str:
    """The version this build of Rewyn writes."""
    from rewyn.core.event import EVENT_SCHEMA_VERSION
    from rewyn.core.manifest import MANIFEST_SCHEMA_VERSION
    from rewyn.core.run import RUN_SCHEMA_VERSION

    if schema == "event":
        return EVENT_SCHEMA_VERSION
    if schema == "run":
        return RUN_SCHEMA_VERSION
    if schema == "manifest":
        return MANIFEST_SCHEMA_VERSION
    if schema == "dataset":
        from rewyn.evaluation.dataset import DATASET_SCHEMA_VERSION

        return DATASET_SCHEMA_VERSION
    if schema == "report":
        from rewyn.evaluation.regression import REPORT_SCHEMA_VERSION

        return REPORT_SCHEMA_VERSION
    if schema == "bundle":
        from rewyn.replay.export import BUNDLE_SCHEMA_VERSION

        return BUNDLE_SCHEMA_VERSION
    from rewyn.skills.skill import SKILL_SCHEMA_VERSION

    return SKILL_SCHEMA_VERSION


def register(schema: Schema, from_version: str, to_version: str, fn: Migrator) -> None:
    """Register a migration from one version of a schema to the next."""
    key = (schema, from_version)
    if key in _MIGRATIONS:
        raise MigrationError(f"a migration from {schema} v{from_version} is already registered")
    if from_version == to_version:
        raise MigrationError(f"{schema} migration from v{from_version} does not advance")
    _MIGRATIONS[key] = (to_version, fn)


def migration(schema: Schema, from_version: str, to_version: str | None = None) -> Any:
    """Decorator form of :func:`register`. Defaults to the next integer version."""

    def decorate(fn: Migrator) -> Migrator:
        target = to_version or str(int(from_version) + 1)
        register(schema, from_version, target, fn)
        return fn

    return decorate


def path(schema: Schema, from_version: str, to_version: str | None = None) -> list[str]:
    """The versions a record passes through on its way to ``to_version``."""
    target = to_version or current_version(schema)
    steps: list[str] = [from_version]
    version = from_version
    while version != target:
        found = _MIGRATIONS.get((schema, version))
        if found is None:
            raise MigrationError(
                f"no migration from {schema} v{version} to v{target}; "
                f"this build writes v{current_version(schema)}"
            )
        version = found[0]
        steps.append(version)
    return steps


def migrate(schema: Schema, record: JSONObject, *, to_version: str | None = None) -> JSONObject:
    """Bring one record up to the current schema version.

    A record already at the target is returned unchanged. A record from a
    *newer* build is left alone rather than mangled: forward compatibility is
    the writer's problem, and silently dropping fields would be worse than
    failing to understand them.
    """
    target = to_version or current_version(schema)
    version = str(record.get("schema_version") or "1")
    if version == target:
        return record
    if _is_newer(version, target):
        return record
    migrated = dict(record)
    while version != target:
        found = _MIGRATIONS.get((schema, version))
        if found is None:
            raise MigrationError(
                f"no migration from {schema} v{version} to v{target}; "
                f"upgrade Rewyn or export the data with the build that wrote it"
            )
        next_version, fn = found
        migrated = fn(migrated)
        migrated["schema_version"] = next_version
        version = next_version
    return migrated


def _is_newer(version: str, target: str) -> bool:
    try:
        return int(version) > int(target)
    except ValueError:
        return False


def registered() -> dict[str, list[str]]:
    """Every registered migration, for ``rewyn doctor``."""
    found: dict[str, list[str]] = {}
    for (schema, from_version), (to_version, _) in sorted(_MIGRATIONS.items()):
        found.setdefault(schema, []).append(f"{from_version}->{to_version}")
    return found


def clear() -> None:
    """Drop every registered migration. For tests."""
    _MIGRATIONS.clear()
