# Integrations

## Concept

Rewyn is meant to sit alongside what you already run, not to replace it.
That means two obligations, and they pull in opposite directions.

The core install must stay lean and provider-agnostic, so nothing here is a
required dependency and every module imports its SDK lazily. But "we don't
depend on your framework" is not the same as "you can use us with your
framework", and only the second is useful to someone who already has an
agent, a vector database and a search cluster.

So each integration speaks a protocol the SDK already defines. Swapping a
backend is a constructor argument, not a migration.

## Minimal example

```python
from rewyn.integrations.redis import RedisMemoryStore
from rewyn.memory import Memory

memory = Memory(namespace="tenant:acme", store=RedisMemoryStore("redis://cache:6379/0"))
```

## Production example

### Data

```python
from rewyn.integrations.pgvector import PgVectorIndex
from rewyn.integrations.redis import RedisCheckpointStore, RedisMemoryStore
from rewyn.integrations.s3 import S3ObjectStore
from rewyn.integrations.search import SearchRetriever

memory = Memory(namespace=tenant, store=RedisMemoryStore(url))  # shared by every worker
checkpointer = Checkpointer(RedisCheckpointStore(url))  # any worker can resume
pipeline = RAGPipeline(index=PgVectorIndex(connection, dimension=1536))
corpus = SearchRetriever("documents", url="http://opensearch:9200")
objects = S3ObjectStore("rewyn-runs", prefix="prod/")  # for the cloud service
```

A file-backed store is fine on a laptop and useless behind a load balancer:
the second replica cannot read the first one's memory. That is the whole
reason Redis is here.

Extras: `pip install "rewyn[redis,s3,search]"`. pgvector needs no extra,
because the connection is yours to pool.

### Other agent frameworks

Give them your tools, with the schema and validation intact:

```python
from rewyn.integrations.frameworks import to_callables, to_openai_tools

to_openai_tools([search, issue_refund])  # also to_anthropic_tools, to_json_schema_tools
to_callables([search])  # name -> validating callable
```

Take theirs:

```python
from rewyn.integrations.frameworks import from_schema

weather = from_schema("weather", "Look up the weather.", their_schema, their_function)
agent = Agent(model=..., tools=[weather])
```

Record theirs:

```python
from rewyn.integrations.frameworks import instrument

graph = instrument(langgraph_app.invoke, framework="langgraph", version="3")
result = graph(question)  # now a recorded Rewyn run
```

`instrument` records the input, output, duration and failure. It cannot see
inside the other framework, so it does not pretend to. Anything the callable
does through Rewyn primitives is recorded in full as usual.

### Agent to agent

```python
from rewyn.integrations.a2a import A2AServer, remote_agent_tool

# Serving: framework agnostic, so mount it wherever you already serve.
server = A2AServer(research_agent, endpoint="https://research.internal/a2a")
app.post("/a2a/run")(server.handle)
app.get("/a2a/card")(server.describe)

# Calling: the remote agent is an ordinary tool.
ask_research = remote_agent_tool(
    "https://research.internal/a2a",
    name="ask_research",
    description="Delegate research to the research service.",
)
agent = Agent(model=..., tools=[ask_research])
```

The call becomes a tool call in the local run, with its own permission
check, cost and latency, and the caller's run id crosses the hop so a trace
survives the service boundary.

### OpenTelemetry

```python
from rewyn.integrations.otel import OTelExporter

with start_run("support") as run:
    OTelExporter().attach(run)
```

## API reference

`rewyn/integrations/redis.py`, `s3.py`, `search.py`, `pgvector.py`,
`frameworks.py`, `a2a.py`, `otel.py`.

## Failure modes

**`MissingDependencyError`.** The extra is not installed. The message names
it.

**A backend does not satisfy the protocol.** The RAG, memory, sandbox and
checkpoint protocols are runtime-checkable, so assert it:
`assert isinstance(my_index, VectorIndex)`.

**S3 reads return `None` for everything.** A missing object reads as `None`,
but a permissions failure raises. If everything is `None`, the keys are
wrong, not the credentials.

**A pgvector insert is rejected.** The embedding's length must match the
column. The error names both numbers.

**An instrumented framework run records nothing inside it.** That is
expected. Rewyn sees the boundary, not the internals. Use Rewyn
primitives inside the callable if you want the detail.

**A remote agent call fails silently.** It does not: `A2AResponse.ok` is
false and `error` says why. Through `remote_agent_tool` it raises, which the
agent loop records as a tool error.
