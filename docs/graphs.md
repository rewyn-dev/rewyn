# Graphs

## Concept

A loop is the right shape when the model should decide what happens next. It
is the wrong shape when you already know: when step two always follows step
one, when a branch depends on a value rather than a judgement, or when a
compliance step must run and must not be skippable by a persuasive prompt.

`Graph` is explicit control flow. Nodes are functions, subgraphs or agents.
Edges are sequential, conditional or routed. Every node emits
`GRAPH_NODE_STARTED` and `GRAPH_NODE_FINISHED`, and the path taken is
recorded, so "why did it skip the risk check?" is answerable.

## Minimal example

```python
from rewyn import Graph
from rewyn.graphs import END

graph = Graph("pipeline")
graph.add_node("research", lambda state: {"facts": search(state["input"])})
graph.add_node("write", lambda state: summarise(state["facts"]), output_key="report")
graph.connect("research", "write")
graph.connect("write", END)

result = graph.run("EV market")
print(result.output, result.path)
```

The first node added is the entry point unless you call `set_entry`.

## Production example

```python
from rewyn import Agent, Graph
from rewyn.graphs import END
from rewyn.human import approve
from rewyn.runtime import Checkpointer

graph = Graph("credit-review", version="2", max_steps=40)

graph.add_node("triage", triage_fn)
graph.add_node("research", Agent(model=..., name="researcher"))  # an agent is a node
graph.add_node("analyze", analyst_fn)
graph.add_node("risk", risk_check)  # deterministic, unskippable


async def sign_off(state):
    if not state["risk_ok"]:
        return f"Declined: {state['reason']}"
    decision = await approve("raise credit limit", risk="high", limit=state["limit"])
    return state["analysis"] if decision.approved else "Held for review"


graph.add_node("sign_off", sign_off, output_key="recommendation")
graph.add_node("general", lambda s: "No credit review needed", output_key="recommendation")

graph.branch("triage", lambda s: "research" if s["needs_credit"] else "general")
graph.connect("research", "analyze")
graph.connect("analyze", "risk")
graph.connect("risk", "sign_off")
graph.connect("sign_off", END)
graph.connect("general", END)

result = await graph.arun(question, checkpointer=Checkpointer())
```

### Conditional edges, parallel waves and retries

```python
graph.connect("analyze", "escalate", when=lambda s: s["confidence"] < 0.6)
graph.add_node("fetch", fetch_fn, retries=3, timeout=20.0)
graph.branch("fan_out", lambda s: ["worker_a", "worker_b"])  # a list runs in parallel
```

### Streaming

```python
async for item in graph.astream({"input": question}):
    ...  # node starts and finishes, model and tool events, token deltas
```

The last item is the `GraphResult`. It shares the stream `Agent.astream`
uses, so an agent node's tokens arrive through the same iterator as the
graph's own progress.

### Resuming

```python
result = await graph.arun(question, checkpointer=Checkpointer(), resume_from=checkpoint_id)
```

## API reference

`rewyn/graphs/graph.py` for `Graph` and `GraphResult`.
`rewyn/graphs/edge.py` for `Edge`, `Branch` and `END`.
`rewyn/graphs/node.py` for `Node` and `make_node`.
`rewyn/graphs/execution.py` for `GraphRunner` and `GraphExecutionError`.

## Failure modes

**`GraphExecutionError: unknown node`.** A router returned a name that is
not a node. Routers return node names, not labels.

**The graph stops at `max_steps`.** A cycle without an exit condition. The
bound is deliberate; find the edge whose condition never goes false.

**A node silently returned `None`.** A node's return value merges into
state. Returning `None` writes nothing, which looks like the node did not
run. Use `output_key` when a node produces a single value.

**Parallel nodes overwrite each other.** Concurrent nodes writing the same
state key race. Give each its own key and merge in a following node.

**Resume replays a node.** Checkpoints are taken between nodes, so resuming
re-runs the node that was in flight. Node functions should be idempotent, or
guard the side effect.
