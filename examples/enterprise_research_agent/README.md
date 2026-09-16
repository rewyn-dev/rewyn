# Enterprise research agent

The golden demo from spec §52. One application that exercises the whole SDK
and then the whole improvement loop on top of it.

```bash
uv run python -m examples.enterprise_research_agent
```

It runs offline against `FakeModel`, so it needs no API key, no account and
no network. Swap the model for `"anthropic:claude-opus-5"` and it is a real
agent; nothing else in the code changes.

## The task

> Analyze Acme's current business relationship with us and recommend whether
> we should increase their credit limit.

## What it exercises

| Stage | Primitive |
| --- | --- |
| Planner routes the work | `Graph` with a conditional edge |
| Research | `Agent` subagent |
| CRM lookup | `MCP` server, in process |
| Document retrieval | `RAG` pipeline over an internal corpus |
| Prior dealings | `Memory` |
| Policy | `Skill` loaded from `SKILL.md` |
| Analysis | `Agent` with `Context`, provenance and a trust boundary |
| Risk | `@tool` plus a guardrail |
| Sign-off | Human approval |

Then the parts that make it improvable:

- **Replay** the run deterministically, with no provider call.
- **Diff** it against a second run to see what changed and what might explain it.
- **Regression** test the run as a golden dataset example, behind a release gate.
- **Manifest** every dependency the run actually used, and detect drift.

## What to look at afterwards

```bash
uv run rewyn runs
uv run rewyn inspect latest
uv run rewyn manifest acme-credit --graph
```
