# MCP

## Concept

The Model Context Protocol is how agents connect to tools and data they do
not own. Rewyn treats it as a first-class source of tools rather than an
integration: an MCP tool becomes a Rewyn `Tool`, goes through the same
permission policy, and emits the same `TOOL_CALLED` and `TOOL_RETURNED`
events as a local function.

That matters for the same reason the rest of this SDK exists. When a
Salesforce MCP server changes its behaviour, you want the run manifest to
show which server, which version, and which tool was called.

## Minimal example

```python
from rewyn.mcp import connect

async with connect("https://crm.internal/mcp", name="crm") as client:
    for tool in client.tools():
        print(tool.name, tool.description)
```

`connect` takes a URL, a command line for stdio, an `MCPServerConfig`, or an
in-process server object.

## Production example

```python
from rewyn import Agent
from rewyn.mcp import connect
from rewyn.tools import MaxRiskLevel, RiskLevel

crm = connect(
    "npx -y @acme/salesforce-mcp",
    name="salesforce",
    version="4",
    tool_prefix="salesforce_",  # keeps the origin visible in transcripts
    risk_level=RiskLevel.HIGH,  # third-party tools are not low risk
)

async with Agent(
    model="anthropic:claude-opus-5",
    mcp=[crm],
    permission_policy=MaxRiskLevel(approve_above=RiskLevel.MEDIUM),
) as agent:
    result = await agent.arun("What is Acme's account owner?")
```

Use `tool_prefix`. Without it, two servers exposing `search` collide, and a
transcript that says `search` does not tell you whose data was read.

### Exposing your own tools

```python
from rewyn.mcp import run_stdio, serve_tools

server = serve_tools([get_customer, issue_refund], name="billing")
run_stdio(server)  # or connect(server) in-process, for tests
```

### Configuration discovery

```python
from rewyn.mcp import discover_configs

for config in discover_configs():  # reads mcp.json
    print(config.name, config.transport)
```

```bash
rewyn mcp                  # list configured servers
rewyn mcp tools salesforce # connect and list its tools
```

## API reference

`rewyn/mcp/client.py` for `MCPClient`, `MCPServerConfig` and `connect`.
`rewyn/mcp/server.py` for `serve_tools` and `run_stdio`.
`rewyn/mcp/discovery.py` for `discover_configs`.
`rewyn/mcp/adapter.py` for the tool translation.

## Failure modes

**`MissingDependencyError`.** MCP support needs the extra:
`pip install "rewyn[mcp]"`.

**The model calls a tool that does not exist.** Almost always the prefix.
With `tool_prefix="salesforce_"` the tool is `salesforce_lookup_account`, not
`lookup_account`. The run shows `unknown tool` as a tool error, and by
default the agent recovers and carries on, so check for tool errors rather
than assuming success.

**The connection leaks.** Use `async with` on the client or the agent. An
agent only closes connections it opened itself.

**A server is slow or hangs.** MCP calls are network calls. Set a timeout on
the agent (`max_time`) and treat a third-party server as untrusted for
latency as well as content.

**Tool schemas are vague.** You do not control a third-party server's
descriptions. Where they are poor, wrap the tool locally with a better one.
