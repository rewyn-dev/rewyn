# Contributing to Rewyn

## Setup

```bash
uv sync --all-extras --all-packages
make check
```

## Ground rules

- **Everything is an event.** A primitive that does not emit execution events
  is incomplete. Use the event names in `rewyn.core.event.EventType`.
- **Provider agnostic.** Provider SDK imports live only in
  `rewyn/models/<provider>.py` and are imported lazily.
- **Modular imports.** Do not add top-level re-exports that force transitive
  imports of heavy subpackages.
- **Fail open.** Instrumentation must never raise into user code.
- **Never persist secrets.** Anything written to disk goes through
  `rewyn.security.redaction`.
- **Typed and tested.** `mypy --strict` and `ruff` must pass. Every new module
  ships with unit tests. Provider adapters are tested against fakes.

## Pull requests

- One logical change per PR, with a conventional-commit style title
  (`feat(context): add budget decisions`).
- Update `CHANGELOG.md` under `Unreleased`.
- Add or update the relevant page under `docs/`.
