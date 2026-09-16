"""Skills (spec §13): reusable capabilities described by ``SKILL.md``.

A skill teaches the agent how to perform a specialised task. It carries
instructions, optional resources and scripts, tool allow-lists,
permissions, dependencies and metadata, and is versioned and fingerprinted.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from rewyn.context.source import ContextItem, ContextKind, Provenance, TrustLevel
from rewyn.core.run import DependencyRef
from rewyn.core.schema import fingerprint
from rewyn.core.types import JSONObject

SKILL_SCHEMA_VERSION = "1"


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    schema_version: str = SKILL_SCHEMA_VERSION
    description: str
    instructions: str
    version: str = "1"
    path: Path | None = None
    resources: list[str] = Field(default_factory=list)
    scripts: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    license: str | None = None
    metadata: JSONObject = Field(default_factory=dict)

    def fingerprint(self) -> str:
        return fingerprint(
            {
                "name": self.name,
                "description": self.description,
                "instructions": self.instructions,
                "version": self.version,
                "resources": sorted(self.resources),
                "scripts": sorted(self.scripts),
                "allowed_tools": sorted(self.allowed_tools),
            }
        )

    @property
    def dependency(self) -> DependencyRef:
        return DependencyRef(
            kind="skill",
            name=self.name,
            version=self.version,
            fingerprint=self.fingerprint(),
            metadata={"path": str(self.path) if self.path else None},
        )

    def summary(self) -> str:
        """Progressive disclosure: name and description only."""
        return f"- {self.name}: {self.description}"

    def as_context_item(self, *, full: bool = True) -> ContextItem:
        content = self.instructions if full else self.summary()
        return ContextItem(
            kind=ContextKind.SKILLS,
            title=f"skill: {self.name} (v{self.version})",
            content=content,
            trust_level=TrustLevel.TRUSTED,
            task_importance=0.9,
            provenance=Provenance.for_content(
                f"skill:{self.name}",
                content,
                version=self.version,
                authority=0.9,
                uri=str(self.path) if self.path else None,
            ),
            metadata={"skill": self.name, "full": full},
        )

    def resolve(self, relative: str) -> Path:
        """Resolve a resource/script path inside the skill directory (no escaping)."""
        if self.path is None:
            raise FileNotFoundError(f"skill {self.name!r} has no directory")
        base = self.path.resolve()
        target = (base / relative).resolve()
        if base not in target.parents and target != base:
            raise PermissionError(f"{relative!r} escapes the skill directory")
        if not target.is_file():
            raise FileNotFoundError(relative)
        return target


def skill(
    name: str,
    description: str,
    instructions: str,
    *,
    version: str = "1",
    allowed_tools: list[str] | None = None,
    permissions: list[str] | None = None,
) -> Skill:
    """Define a skill in code (no ``SKILL.md`` needed)."""
    return Skill(
        name=name,
        description=description,
        instructions=instructions,
        version=version,
        allowed_tools=list(allowed_tools or []),
        permissions=list(permissions or []),
    )
