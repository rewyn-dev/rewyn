# Memory

## Concept

Context is what the model sees this turn. Memory is what survives between
turns and between runs. Conflating them produces a system that either
forgets everything or drags its entire history into every prompt.

Rewyn splits memory by what it is for. Working and short-term memory hold
the current exchange. Episodic memory records what happened and whether it
worked. Semantic memory holds facts. Procedural memory holds learned
how-to. Each read emits `MEMORY_READ` and each write `MEMORY_WRITE`, so a
recalled memory that steered an answer is visible in the run.

## Minimal example

```python
from rewyn.memory import Memory

memory = Memory()
await memory.remember("Acme prefers invoices over card payments", importance=0.8)

hits = await memory.recall("how does Acme pay?")
print(hits[0].item.content, hits[0].score)
```

## Production example

```python
from rewyn.memory import FileStore, Memory, MemoryKind

memory = Memory(
    name="account-memory",
    namespace=f"tenant:{tenant_id}",  # isolation between customers
    store=FileStore(),  # survives process restarts
)

await memory.remember(
    "Credit limit raised to 250k after the 2026 contract renewal",
    kind=MemoryKind.EPISODIC,
    importance=0.9,
    tags=["acme", "credit"],
)

agent = Agent(model=..., memory=memory)  # recall feeds the context automatically
```

Attaching memory to an agent adds it as a context source, so relevant items
are retrieved and budgeted like any other context. The agent also writes
back after each run unless you pass `memory_autosave=False`.

### Namespaces

`namespace` is the tenant boundary and it is enforced, not advisory. Items
are stamped with their namespace on write, and reads, deletes and clears
refuse to cross it, so two tenants can share one store safely:

```python
a = Memory(namespace="tenant:a", store=FileStore())
b = Memory(namespace="tenant:b", store=FileStore())

await a.remember("Acme's margin is 42%")
assert await b.recall("margin") == []
```

Stores that can isolate natively do. `FileStore` gives each namespace its
own file and `RedisMemoryStore` its own key prefix; anything else is
filtered, with the filter applied before the limit so a busy tenant cannot
push a quiet one out of its own result page.

Set it from the start. Migrating later means rewriting keys.

## API reference

`rewyn/memory/memory.py` for `Memory`, `MemoryItem`, `MemoryHit` and
`MemoryKind`.
`rewyn/memory/providers/` for `InMemoryStore` and `FileStore`.
`rewyn/integrations/redis.py` for `RedisMemoryStore`, which is what a
fleet of workers needs: a file store is fine on a laptop and useless behind
a load balancer.
`rewyn/memory/episodic.py` for `record_episode` and `similar_episodes`.
`rewyn/memory/semantic.py` for `remember_fact` and `facts_about`.

## Failure modes

**Memory grows without bound.** Nothing expires by default. Set
`importance` honestly and prune on a schedule; recall ranks by relevance and
importance, so low-value writes dilute results rather than disappearing.

**Recall returns nothing useful.** The default scoring is lexical. For
semantic recall over a large store, back memory with a retriever instead.

**Memories leak across tenants.** They should not: namespaces are enforced.
If you are seeing crosstalk, check that both sides construct `Memory` with
the namespace rather than reaching into the store directly.

**The agent remembers something wrong.** Autosave writes the exchange after
every run, including the failed ones. Episodic items record `success`, so
filter on it when recalling for guidance.

**Nothing persists.** The default store is in-memory. Pass
`store=FileStore()` for anything that should outlive the process.
