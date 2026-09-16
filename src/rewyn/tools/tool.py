"""First-class tools (spec §23).

A :class:`Tool` wraps a Python callable with metadata (name, description,
JSON schema, permissions, version, owner, risk level) and validates
arguments before invocation. Use the :func:`tool` decorator::

    @tool
    def get_customer(customer_id: str) -> dict:
        \"\"\"Fetch a customer record.\"\"\"
        ...

    @tool(risk_level="high", permissions=["billing:write"])
    def issue_refund(customer_id: str, amount: float) -> str: ...
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, overload

from pydantic import BaseModel, ValidationError

from rewyn.core.run import DependencyRef
from rewyn.core.schema import fingerprint, format_validation_error, model_for_callable
from rewyn.core.sync import run_sync
from rewyn.core.types import JSONObject, RewynError
from rewyn.models.base import ToolCallPart, ToolResultPart, ToolSpec
from rewyn.tools.permissions import RiskLevel

ToolCall = ToolCallPart
ToolResult = ToolResultPart


class ToolArgumentError(RewynError):
    """Arguments did not match the tool's schema."""

    def __init__(self, tool_name: str, errors: Sequence[str]) -> None:
        super().__init__(f"invalid arguments for tool {tool_name!r}: " + "; ".join(errors))
        self.tool_name = tool_name
        self.errors = list(errors)


def _describe(fn: Callable[..., Any]) -> str:
    doc = inspect.getdoc(fn) or ""
    return doc.split("\n\n", 1)[0].strip()


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    fn: Callable[..., Any]
    parameters: JSONObject
    version: str = "1"
    owner: str | None = None
    risk_level: RiskLevel = RiskLevel.LOW
    permissions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    strict: bool = False
    timeout: float | None = None
    source: str = "python"
    cost_per_call: float = 0.0
    """What one call costs, in USD. Commercial metadata, so not fingerprinted."""

    arguments_model: type[BaseModel] | None = field(default=None, repr=False)

    # Metadata ------------------------------------------------------------------
    @property
    def is_async(self) -> bool:
        return inspect.iscoroutinefunction(self.fn)

    @property
    def is_streaming(self) -> bool:
        """True for a tool written as an async generator (spec §41).

        Each yield is an interim update; the last one is the result. Long
        tools can then report progress without a separate callback channel.
        """
        return inspect.isasyncgenfunction(self.fn)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            strict=self.strict,
        )

    def price(self, seconds: float) -> float:
        """Cost of one call: the declared price, or a registered unit price."""
        from rewyn.models.pricing import compute_unit_cost

        if self.cost_per_call:
            return self.cost_per_call
        return compute_unit_cost("tool", self.name, calls=1, seconds=seconds)

    def fingerprint(self) -> str:
        return fingerprint(
            {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
                "version": self.version,
                "source": self.source,
            }
        )

    @property
    def dependency(self) -> DependencyRef:
        return DependencyRef(
            kind="tool",
            name=self.name,
            version=self.version,
            fingerprint=self.fingerprint(),
            metadata={"risk_level": self.risk_level.value, "source": self.source},
        )

    # Invocation ------------------------------------------------------------------
    def validate_arguments(self, arguments: JSONObject) -> JSONObject:
        if self.arguments_model is None:
            return dict(arguments)
        try:
            parsed = self.arguments_model.model_validate(arguments)
        except ValidationError as exc:
            raise ToolArgumentError(self.name, format_validation_error(exc)) from exc
        return {name: getattr(parsed, name) for name in type(parsed).model_fields}

    async def ainvoke(self, **arguments: Any) -> Any:
        """Validate ``arguments`` and call the underlying function."""
        if self.is_streaming:
            result: Any = None
            async for update in self.astream(**arguments):
                result = update
            return result
        kwargs = self.validate_arguments(arguments)
        coro = self.fn(**kwargs) if self.is_async else asyncio.to_thread(self.fn, **kwargs)
        if self.timeout is not None:
            return await asyncio.wait_for(coro, timeout=self.timeout)
        return await coro

    async def astream(self, **arguments: Any) -> AsyncIterator[Any]:
        """Yield each interim update from a streaming tool. The last is the result."""
        if not self.is_streaming:
            yield await self.ainvoke(**arguments)
            return
        kwargs = self.validate_arguments(arguments)
        agen = self.fn(**kwargs)
        if self.timeout is None:
            async for update in agen:
                yield update
            return
        # A streaming tool still gets one overall deadline, or a generator
        # that stalls between yields would hold the loop open indefinitely.
        async with asyncio.timeout(self.timeout):
            async for update in agen:
                yield update

    def invoke(self, **arguments: Any) -> Any:
        return run_sync(self.ainvoke(**arguments))

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Call the wrapped function directly (no validation, no events)."""
        return self.fn(*args, **kwargs)


def make_tool(
    fn: Callable[..., Any],
    *,
    name: str | None = None,
    description: str | None = None,
    version: str = "1",
    owner: str | None = None,
    risk_level: RiskLevel | str = RiskLevel.LOW,
    permissions: Sequence[str] = (),
    tags: Sequence[str] = (),
    strict: bool = False,
    timeout: float | None = None,
    cost_per_call: float = 0.0,
    parameters: JSONObject | None = None,
) -> Tool:
    """Build a :class:`Tool` from a callable, deriving the schema from its signature."""
    arguments_model = model_for_callable(fn) if parameters is None else None
    schema = parameters or _schema_from_model(arguments_model)
    return Tool(
        name=name or fn.__name__,
        description=description if description is not None else _describe(fn),
        fn=fn,
        parameters=schema,
        version=version,
        owner=owner,
        risk_level=RiskLevel(risk_level),
        permissions=list(permissions),
        tags=list(tags),
        strict=strict,
        timeout=timeout,
        cost_per_call=cost_per_call,
        arguments_model=arguments_model,
    )


def _schema_from_model(model: type[BaseModel] | None) -> JSONObject:
    from rewyn.core.schema import schema_for_type

    if model is None:
        return {"type": "object", "properties": {}}
    schema = schema_for_type(model)
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return schema


@overload
def tool(fn: Callable[..., Any], /) -> Tool: ...


@overload
def tool(
    *,
    name: str | None = None,
    description: str | None = None,
    version: str = "1",
    owner: str | None = None,
    risk_level: RiskLevel | str = RiskLevel.LOW,
    permissions: Sequence[str] = (),
    tags: Sequence[str] = (),
    strict: bool = False,
    timeout: float | None = None,
    cost_per_call: float = 0.0,
) -> Callable[[Callable[..., Any]], Tool]: ...


def tool(fn: Callable[..., Any] | None = None, /, **options: Any) -> Any:
    """Decorator turning a function into a :class:`Tool` (usable bare or with options)."""
    if fn is not None:
        return make_tool(fn, **options)

    def decorate(inner: Callable[..., Any]) -> Tool:
        return make_tool(inner, **options)

    return decorate


def as_tool(value: Tool | Callable[..., Any]) -> Tool:
    return value if isinstance(value, Tool) else make_tool(value)
