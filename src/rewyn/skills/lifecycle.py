"""Skill lifecycle: loading, progressive disclosure and activation (spec §13, §14).

``SkillManager`` gives an agent:

- a ``SKILL_LOADED`` event and dependency record per available skill;
- context items: in ``progressive`` mode only name + description are shown
  until the agent calls ``load_skill``; in ``eager`` mode full instructions
  are included up front;
- two tools, ``load_skill`` and ``read_skill_file``, which emit
  ``SKILL_ACTIVATED`` and expose a skill's resources/scripts safely.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from rewyn.context.source import ContextItem, ContextKind, TrustLevel
from rewyn.core.event import EventType
from rewyn.core.run import Run, aensure_run
from rewyn.skills.registry import SkillRegistry
from rewyn.skills.skill import Skill
from rewyn.tools.tool import Tool, make_tool

DisclosureMode = Literal["progressive", "eager"]

MAX_FILE_CHARS = 20_000


class SkillManager:
    def __init__(
        self,
        skills: Iterable[Skill | str | Path] = (),
        *,
        mode: DisclosureMode = "progressive",
    ) -> None:
        self.registry = SkillRegistry(skills)
        self.mode: DisclosureMode = mode
        self.active: set[str] = set()
        self._loaded_runs: set[str] = set()

    # Events ----------------------------------------------------------------------
    def load(self, run: Run) -> None:
        """Record every available skill on ``run`` (idempotent per run)."""
        if run.id in self._loaded_runs:
            return
        self._loaded_runs.add(run.id)
        for skill in self.registry:
            run.add_dependency(skill.dependency)
            run.emit(
                EventType.SKILL_LOADED,
                {
                    "skill": skill.name,
                    "version": skill.version,
                    "fingerprint": skill.fingerprint(),
                    "mode": self.mode,
                    "description": skill.description,
                    "resources": skill.resources,
                    "scripts": skill.scripts,
                    "allowed_tools": skill.allowed_tools,
                },
            )
            if self.mode == "eager":
                self.active.add(skill.name)

    async def activate(self, name: str) -> Skill:
        skill = self.registry[name]
        async with aensure_run("skill") as run:
            first = name not in self.active
            self.active.add(name)
            run.emit(
                EventType.SKILL_ACTIVATED,
                {
                    "skill": skill.name,
                    "version": skill.version,
                    "fingerprint": skill.fingerprint(),
                    "first_activation": first,
                },
            )
        return skill

    # Context -----------------------------------------------------------------------
    def context_items(self) -> list[ContextItem]:
        items: list[ContextItem] = []
        if not len(self.registry):
            return items
        if self.mode == "progressive":
            available = [s for s in self.registry if s.name not in self.active]
            if available:
                listing = "\n".join(s.summary() for s in available)
                items.append(
                    ContextItem(
                        kind=ContextKind.SKILLS,
                        title="available skills",
                        content=(
                            "You can load these skills with the load_skill tool when they are "
                            "relevant to the task:\n" + listing
                        ),
                        trust_level=TrustLevel.TRUSTED,
                        task_importance=0.8,
                        required=True,
                    )
                )
        for skill in self.registry:
            if skill.name in self.active:
                item = skill.as_context_item(full=True)
                item.required = True
                items.append(item)
        return items

    # Tools ---------------------------------------------------------------------------
    def tools(self) -> list[Tool]:
        if not len(self.registry):
            return []
        manager = self

        async def load_skill(name: str) -> str:
            """Load a skill's full instructions by name. Use before performing its task."""
            skill = await manager.activate(name)
            return skill.instructions

        def read_skill_file(skill: str, path: str) -> str:
            """Read a resource or script file that belongs to a loaded skill."""
            target = manager.registry[skill].resolve(path)
            text = target.read_text(encoding="utf-8", errors="replace")
            return text[:MAX_FILE_CHARS]

        tools = [
            make_tool(load_skill, name="load_skill", tags=["skills"], risk_level="low"),
            make_tool(read_skill_file, name="read_skill_file", tags=["skills"], risk_level="low"),
        ]
        return tools if self.mode == "progressive" else tools[1:]
