# State

## Concept

State is the working memory of one execution: what the nodes of a graph pass
between them, what a loop accumulates. The reason it is a type rather than a
dict is that every mutation is versioned and every version can be
snapshotted. That is what makes a checkpoint meaningful and a resumed run
correct.

Every update emits `STATE_UPDATED` with the keys that changed, so the diff
between two runs can tell you the state diverged before the output did.

## Minimal example

```python
from rewyn.core.state import State

state = State({"customer": "Acme"})
state.update({"limit": 100_000})

print(state["limit"], state.version)
snapshot = state.snapshot("after lookup")
```

## Production example

```python
from rewyn.runtime import Checkpointer, FileCheckpointStore

checkpointer = Checkpointer(FileCheckpointStore())

result = await graph.arun({"customer": "Acme"}, checkpointer=checkpointer)

# Later, in another process:
resumed = await graph.arun(resume_from=checkpoint_id, checkpointer=checkpointer)
```

Graph nodes write state by returning a mapping. A node that returns a single
value writes it under its `output_key`.

```python
graph.add_node("lookup", lambda s: {"account": crm.get(s["customer"])})
graph.add_node("score", lambda s: bureau(s["account"]), output_key="score")
```

### History

```python
for snapshot in state.history:
    print(snapshot.version, snapshot.label, snapshot.fingerprint)
```

## API reference

`rewyn/core/state.py` for `State` and `StateSnapshot`.
`rewyn/graphs/state.py` for `NodeContext`, `INPUT_KEY` and `OUTPUT_KEY`.
`rewyn/runtime/checkpoint.py` for `Checkpoint` and `Checkpointer`.
`rewyn/runtime/persistence.py` for `FileCheckpointStore`.

## Failure modes

**A key is missing.** Nodes run in graph order, not source order. A node
reading a key written by a later node raises `KeyError`. Use
`state.get(key, default)` where a node is genuinely optional.

**State grows large.** Everything in state is snapshotted into every
checkpoint and serialised into events. Keep large blobs out of it; store a
reference instead.

**Values are not serialisable.** Snapshots and checkpoints go through JSON.
Put plain data in state, not open connections or file handles.

**Concurrent nodes clobber a key.** Parallel branches writing the same key
race. Give each branch its own key.

**A resumed run repeats work.** Checkpoints land between nodes, so the node
that was in flight runs again. Make node functions idempotent.
