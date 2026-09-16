"""Tool permission policies (spec §23, §37).

A policy decides whether a tool call may proceed, and whether it needs a
human decision first. Decisions are recorded as events by the executor.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from rewyn.core.types import JSONObject

if TYPE_CHECKING:  # pragma: no cover
    from rewyn.tools.tool import Tool


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _RANK[self]


_RANK = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}


class PermissionDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    reason: str = ""
    requires_approval: bool = False
    policy: str = ""

    @classmethod
    def allow(cls, policy: str, reason: str = "allowed") -> PermissionDecision:
        return cls(allowed=True, reason=reason, policy=policy)

    @classmethod
    def deny(cls, policy: str, reason: str) -> PermissionDecision:
        return cls(allowed=False, reason=reason, policy=policy)

    @classmethod
    def approval(cls, policy: str, reason: str) -> PermissionDecision:
        return cls(allowed=True, reason=reason, requires_approval=True, policy=policy)


@runtime_checkable
class PermissionPolicy(Protocol):
    def check(self, tool: Tool, arguments: JSONObject) -> PermissionDecision: ...


class AllowAll:
    def check(self, tool: Tool, arguments: JSONObject) -> PermissionDecision:
        return PermissionDecision.allow("allow_all")


class DenyAll:
    def check(self, tool: Tool, arguments: JSONObject) -> PermissionDecision:
        return PermissionDecision.deny("deny_all", "all tool calls are denied by policy")


class AllowList:
    """Allow only the named tools."""

    def __init__(self, names: Iterable[str]) -> None:
        self.names = frozenset(names)

    def check(self, tool: Tool, arguments: JSONObject) -> PermissionDecision:
        if tool.name in self.names:
            return PermissionDecision.allow("allow_list")
        return PermissionDecision.deny("allow_list", f"tool {tool.name!r} is not on the allow list")


class MaxRiskLevel:
    """Deny tools above ``deny_above``; require approval above ``approve_above``."""

    def __init__(
        self,
        deny_above: RiskLevel = RiskLevel.CRITICAL,
        *,
        approve_above: RiskLevel | None = None,
    ) -> None:
        self.deny_above = RiskLevel(deny_above)
        self.approve_above = RiskLevel(approve_above) if approve_above else None

    def check(self, tool: Tool, arguments: JSONObject) -> PermissionDecision:
        level = RiskLevel(tool.risk_level)
        if level.rank > self.deny_above.rank:
            return PermissionDecision.deny(
                "max_risk_level", f"tool {tool.name!r} risk {level} exceeds {self.deny_above}"
            )
        if self.approve_above is not None and level.rank > self.approve_above.rank:
            return PermissionDecision.approval(
                "max_risk_level", f"tool {tool.name!r} risk {level} requires human approval"
            )
        return PermissionDecision.allow("max_risk_level")


class RequirePermissions:
    """Require every permission declared by the tool to be granted."""

    def __init__(self, granted: Iterable[str]) -> None:
        self.granted = frozenset(granted)

    def check(self, tool: Tool, arguments: JSONObject) -> PermissionDecision:
        missing = sorted(set(tool.permissions) - self.granted)
        if missing:
            return PermissionDecision.deny(
                "require_permissions", f"tool {tool.name!r} needs permissions {missing}"
            )
        return PermissionDecision.allow("require_permissions")


class CompositePolicy:
    """All policies must allow; approval is required if any policy asks for it."""

    def __init__(self, policies: Sequence[PermissionPolicy]) -> None:
        self.policies = list(policies)

    def check(self, tool: Tool, arguments: JSONObject) -> PermissionDecision:
        needs_approval: PermissionDecision | None = None
        for policy in self.policies:
            decision = policy.check(tool, arguments)
            if not decision.allowed:
                return decision
            if decision.requires_approval and needs_approval is None:
                needs_approval = decision
        return needs_approval or PermissionDecision.allow("composite")
