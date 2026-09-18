# Contributing to Rewyn

## Setup

```bash
uv sync --all-extras
make check
```

The console (`web/`) needs **Node 24**, which is what CI uses: vitest 5
requires Node ^22.12 or ^24, and Node 20's npm 10 cannot resolve the
dependency tree at all. `make check-web` and `make build-web` do the rest.

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
- Add or update the relevant page under `docs/`. Their Python snippets are
  parsed and checked against the package by `tests/examples/test_docs.py`, so
  a renamed parameter breaks the test rather than silently breaking the docs.

## Sign your work

Rewyn uses the [Developer Certificate of Origin](https://developercertificate.org/).
It is a short statement that you wrote the patch, or otherwise have the right
to submit it under this project's licence. You agree to it by adding a
`Signed-off-by` line to each commit:

```bash
git commit -s -m "feat(context): add budget decisions"
```

which appends:

```
Signed-off-by: Your Name <your.email@example.com>
```

Use your real name. We ask for the DCO rather than a contributor licence
agreement deliberately: there is nothing to sign, nothing to send, and you
keep the copyright in your contribution.

## Reporting a security issue

Do not open a public issue. See [SECURITY.md](SECURITY.md).
