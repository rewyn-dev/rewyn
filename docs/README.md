# Rewyn documentation

Rewyn is a provider-agnostic SDK for building AI agents and, more
importantly, for understanding what they did afterwards. These pages teach
the engineering, not just the call signatures.

Every page follows the same shape: what the thing is and why it exists, the
smallest useful example, what it looks like in production, where to find the
API, and the ways it goes wrong.

## Start here

- [Getting started](getting-started.md)

## Building

| Page | What it covers |
| --- | --- |
| [Models](models.md) | One interface across providers, cost and structured output |
| [Agents](agents.md) | The loop that turns a model plus tools into behaviour |
| [Tools](tools.md) | Typed, versioned, permissioned functions a model can call |
| [Context engineering](context-engineering.md) | Deciding what goes in the prompt, and proving it later |
| [Memory](memory.md) | What the system remembers between runs |
| [RAG](rag.md) | Retrieval as an observable pipeline, not a black box |
| [MCP](mcp.md) | Connecting to Model Context Protocol servers |
| [Skills](skills.md) | Versioned instructions loaded from `SKILL.md` |

## Controlling

| Page | What it covers |
| --- | --- |
| [Loops](loops.md) | ReAct, plan-execute, reflection, and budgets |
| [Graphs](graphs.md) | Explicit control flow when a loop is not enough |
| [Subagents](subagents.md) | Delegating a scoped task |
| [Handoffs](handoffs.md) | Transferring a conversation to another agent |
| [State](state.md) | Versioned, snapshot-able execution state |
| [Sandbox](sandbox.md) | Running generated code without trusting it |
| [Guardrails](guardrails.md) | Input and output policy, recorded as decisions |
| [Human in the loop](human-in-the-loop.md) | Approvals, feedback and corrections |

## Improving

| Page | What it covers |
| --- | --- |
| [Replay](replay.md) | Reproducing a run exactly, or changing one component |
| [Evaluation](evaluation.md) | Deterministic metrics, LLM judges, custom evaluators |
| [Regression](regression.md) | Datasets, baselines and release gates |

## Operating

| Page | What it covers |
| --- | --- |
| [Console](ui.md) | The local UI and the cloud console: reading what a run did |
| [Production](production.md) | Recording, sampling, cost, migrations and reliability |
| [Integrations](integrations.md) | Redis, S3, search, vector databases, other frameworks |
| [Security](security.md) | Secrets, trust boundaries and prompt injection |
| [Cloud](cloud.md) | The optional hosted platform |

## The whole thing at once

`examples/enterprise_research_agent/` is the golden demo: one application
that uses every subsystem above and then replays, diffs, evaluates and gates
itself. It runs offline.

```bash
uv run python -m examples.enterprise_research_agent
```
