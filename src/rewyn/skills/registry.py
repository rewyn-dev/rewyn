"""Skill registry."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

from rewyn.core.types import ConfigurationError
from rewyn.skills.loader import load_skill
from rewyn.skills.skill import Skill


def as_skill(item: Skill | str | Path) -> Skill:
    return item if isinstance(item, Skill) else load_skill(item)


class SkillRegistry:
    def __init__(self, skills: Iterable[Skill | str | Path] = ()) -> None:
        self._skills: dict[str, Skill] = {}
        for item in skills:
            self.register(item)

    def register(self, item: Skill | str | Path, *, replace: bool = False) -> Skill:
        skill = as_skill(item)
        if skill.name in self._skills and not replace:
            raise ConfigurationError(f"skill {skill.name!r} is already registered")
        self._skills[skill.name] = skill
        return skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def __getitem__(self, name: str) -> Skill:
        try:
            return self._skills[name]
        except KeyError:
            raise KeyError(f"unknown skill {name!r}") from None

    def __contains__(self, name: object) -> bool:
        return name in self._skills

    def __iter__(self) -> Iterator[Skill]:
        return iter(self._skills.values())

    def __len__(self) -> int:
        return len(self._skills)

    def names(self) -> list[str]:
        return list(self._skills)
