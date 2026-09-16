"""First-class tools: definition, registry, permissions and execution."""

from rewyn.tools.execution import ToolExecutionError, ToolExecutor
from rewyn.tools.permissions import (
    AllowAll,
    AllowList,
    CompositePolicy,
    DenyAll,
    MaxRiskLevel,
    PermissionDecision,
    PermissionPolicy,
    RequirePermissions,
    RiskLevel,
)
from rewyn.tools.registry import ToolRegistry
from rewyn.tools.tool import (
    Tool,
    ToolArgumentError,
    ToolCall,
    ToolResult,
    as_tool,
    make_tool,
    tool,
)

__all__ = [
    "AllowAll",
    "AllowList",
    "CompositePolicy",
    "DenyAll",
    "MaxRiskLevel",
    "PermissionDecision",
    "PermissionPolicy",
    "RequirePermissions",
    "RiskLevel",
    "Tool",
    "ToolArgumentError",
    "ToolCall",
    "ToolExecutionError",
    "ToolExecutor",
    "ToolRegistry",
    "ToolResult",
    "as_tool",
    "make_tool",
    "tool",
]
