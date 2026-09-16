"""Convert between MCP and Rewyn tool representations."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from rewyn.core.types import JSONObject, RewynError
from rewyn.tools.permissions import RiskLevel
from rewyn.tools.tool import Tool

if TYPE_CHECKING:  # pragma: no cover
    from rewyn.mcp.client import MCPClient


class MCPToolError(RewynError):
    """The MCP server reported ``is_error`` for a tool call."""


def result_to_value(result: Any) -> Any:
    """Turn a ``CallToolResult`` into plain data (structured content preferred)."""
    structured = getattr(result, "structured_content", None)
    if structured:
        # The MCP SDK wraps non-object return values as {"result": value}.
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    texts: list[str] = []
    other: list[JSONObject] = []
    for block in getattr(result, "content", None) or []:
        block_type = getattr(block, "type", "")
        if block_type == "text":
            texts.append(block.text)
        elif hasattr(block, "model_dump"):
            other.append(block.model_dump(mode="json", exclude_none=True))
    if texts and not other:
        joined = "\n".join(texts)
        try:
            return json.loads(joined)
        except (json.JSONDecodeError, ValueError):
            return joined
    return {"text": "\n".join(texts), "content": other}


def mcp_tool_to_rewyn(
    client: MCPClient,
    mcp_tool: Any,
    *,
    prefix: str | None = None,
    risk_level: RiskLevel = RiskLevel.MEDIUM,
) -> Tool:
    """Wrap an MCP tool definition as a Rewyn :class:`Tool` bound to ``client``."""
    remote_name: str = mcp_tool.name
    local_name = f"{prefix}{remote_name}" if prefix else remote_name
    schema: JSONObject = dict(getattr(mcp_tool, "input_schema", None) or {})
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    annotations = getattr(mcp_tool, "annotations", None)
    level = risk_level
    if annotations is not None:
        if getattr(annotations, "destructive_hint", None):
            level = RiskLevel.HIGH
        elif getattr(annotations, "read_only_hint", None):
            level = RiskLevel.LOW

    async def _call(**arguments: Any) -> Any:
        return await client.call_tool_raw(remote_name, arguments)

    _call.__name__ = local_name
    fn: Callable[..., Awaitable[Any]] = _call
    return Tool(
        name=local_name,
        description=getattr(mcp_tool, "description", None) or "",
        fn=fn,
        parameters=schema,
        version=client.config.version,
        owner=client.config.name,
        risk_level=level,
        permissions=[f"mcp:{client.config.name}"],
        tags=["mcp"],
        source=f"mcp:{client.config.name}",
        arguments_model=None,
    )
