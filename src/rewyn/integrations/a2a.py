"""Agent-to-agent interfaces (spec §3.4, §51).

MCP connects an agent to tools. The other half of an open ecosystem is
agents talking to agents: one team's service calls another team's agent
without either importing the other's code.

The shape is deliberately small, because the protocols in this space are
still moving and a thick abstraction would age badly:

**An agent card** describes what an agent does and what it accepts, which is
what a caller needs before it can call anything.

**A server** exposes a Rewyn agent over plain JSON, mountable in whatever
web framework you already run.

**A client tool** turns a remote agent into an ordinary Rewyn `Tool`, so a
local agent delegates to a remote one exactly as it would to a subagent, and
the call is recorded as a tool call with its own cost and latency.

Calls carry the run id, so a trace survives the hop between services.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.schema import fingerprint, to_jsonable
from rewyn.core.types import JSONObject, RewynError

PROTOCOL_VERSION = "1"
RUN_HEADER = "X-Rewyn-Run-Id"
PARENT_HEADER = "X-Rewyn-Parent-Run-Id"


class A2AError(RewynError):
    """A remote agent call failed."""


class AgentCard(BaseModel):
    """What a remote agent advertises about itself."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    version: str = "1"
    protocol_version: str = PROTOCOL_VERSION
    input_schema: JSONObject = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"input": {"type": "string"}},
            "required": ["input"],
        }
    )
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    endpoint: str | None = None

    def fingerprint(self) -> str:
        return fingerprint(self.model_dump(mode="json", exclude={"endpoint"}))


class A2ARequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: Any
    run_id: str | None = None
    """The caller's run, so both sides of the hop can be stitched together."""

    metadata: JSONObject = Field(default_factory=dict)


class A2AResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output: Any = None
    run_id: str | None = None
    agent: str = ""
    stop_reason: str = "completed"
    cost: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def card_for(agent: Any, *, endpoint: str | None = None) -> AgentCard:
    """Describe a Rewyn agent for publication."""
    return AgentCard(
        name=agent.name,
        description=agent.instructions or "",
        version=getattr(agent, "version", "1"),
        skills=[s.name for s in getattr(agent.skills, "registry", [])],
        tools=list(agent.registry.names()),
        endpoint=endpoint,
    )


class A2AServer:
    """Expose one Rewyn agent over JSON.

    Framework agnostic on purpose: :meth:`handle` takes a dict and returns a
    dict, so mounting it in FastAPI, Flask, Django or a Lambda is three
    lines and none of them are here.
    """

    def __init__(self, agent: Any, *, endpoint: str | None = None) -> None:
        self.agent = agent
        self.card = card_for(agent, endpoint=endpoint)

    async def handle(self, payload: JSONObject) -> JSONObject:
        """Run the agent for one request. Failures are returned, not raised."""
        from rewyn.core.run import start_run

        try:
            request = A2ARequest.model_validate(payload)
        except Exception as exc:
            return A2AResponse(agent=self.card.name, error=f"invalid request: {exc}").model_dump(
                mode="json"
            )

        async with start_run(
            self.card.name,
            tags=("a2a",),
            metadata={"protocol": "a2a", "caller_run_id": request.run_id},
            input=request.input,
        ) as run:
            try:
                result = await self.agent.arun(request.input)
            except Exception as exc:
                return A2AResponse(
                    agent=self.card.name,
                    run_id=run.id,
                    stop_reason="error",
                    error=f"{type(exc).__name__}: {exc}",
                ).model_dump(mode="json")
            run.manifest.output = result.output
            return A2AResponse(
                output=result.output,
                run_id=run.id,
                agent=self.card.name,
                stop_reason=str(getattr(result.stop_reason, "value", result.stop_reason)),
                cost=float(getattr(result, "cost", 0.0) or 0.0),
            ).model_dump(mode="json")

    def describe(self) -> JSONObject:
        """The agent card, for a discovery endpoint."""
        return self.card.model_dump(mode="json")


class A2AClient:
    """Call a remote agent over JSON."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: Any = None,
        timeout: float = 60.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.headers = dict(headers or {})
        self._client = client

    def _ensure_client(self) -> Any:
        if self._client is None:
            from rewyn.core.types import MissingDependencyError

            try:
                import httpx
            except ImportError as exc:  # pragma: no cover - needs the extra uninstalled
                raise MissingDependencyError("httpx", "remote") from exc
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def describe(self) -> AgentCard:
        """Fetch the remote agent's card."""
        response = await self._ensure_client().get(f"{self.endpoint}/card", headers=self.headers)
        return AgentCard.model_validate(response.json())

    async def call(self, input: Any, **metadata: Any) -> A2AResponse:
        """Invoke the remote agent, carrying the current run id across the hop."""
        from rewyn.core.run import current_run

        run = current_run()
        headers = dict(self.headers)
        if run is not None:
            headers[RUN_HEADER] = run.id
        request = A2ARequest(
            input=to_jsonable(input),
            run_id=run.id if run is not None else None,
            metadata=to_jsonable(metadata),
        )
        try:
            response = await self._ensure_client().post(
                f"{self.endpoint}/run",
                json=request.model_dump(mode="json"),
                headers=headers,
            )
        except Exception as exc:
            return A2AResponse(error=f"{type(exc).__name__}: {exc}", stop_reason="error")
        if response.status_code >= 400:
            return A2AResponse(error=f"HTTP {response.status_code}", stop_reason="error")
        return A2AResponse.model_validate(response.json())

    async def aclose(self) -> None:
        if self._client is not None and hasattr(self._client, "aclose"):
            await self._client.aclose()


def remote_agent_tool(
    endpoint: str,
    *,
    name: str,
    description: str,
    client: Any = None,
    risk_level: str = "medium",
) -> Any:
    """A tool that delegates to a remote agent.

    The remote call becomes an ordinary tool call in the local run, with its
    own permission check, cost and latency, which is what makes delegating
    across a service boundary as inspectable as delegating to a subagent.
    """
    from rewyn.tools.tool import make_tool

    remote = A2AClient(endpoint, client=client)

    async def call_remote(task: str) -> str:
        """Send a task to a remote agent and return its answer."""
        response = await remote.call(task)
        if not response.ok:
            raise A2AError(f"remote agent {name!r} failed: {response.error}")
        return str(response.output)

    return make_tool(
        call_remote,
        name=name,
        description=description,
        risk_level=risk_level,
    )
