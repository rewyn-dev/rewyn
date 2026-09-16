"""Find skills on disk.

Default search paths: ``./skills``, ``./.rewyn/skills``,
``$REWYN_HOME/skills``, ``~/.rewyn/skills`` and ``REWYN_SKILLS_PATH``
(``os.pathsep`` separated). Each subdirectory with a ``SKILL.md`` is a skill.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from rewyn.core.settings import get_settings
from rewyn.skills.loader import SkillLoadError, load_skill
from rewyn.skills.skill import Skill


def default_skill_paths() -> list[Path]:
    paths = [
        Path("skills"),
        Path(".rewyn") / "skills",
        get_settings().home / "skills",
        Path.home() / ".rewyn" / "skills",
    ]
    extra = os.environ.get("REWYN_SKILLS_PATH")
    if extra:
        paths.extend(Path(p) for p in extra.split(os.pathsep) if p)
    return paths


def discover_skills(
    paths: Iterable[Path] | None = None, *, errors: list[str] | None = None
) -> list[Skill]:
    """Load every skill found under the search paths; first name wins."""
    found: dict[str, Skill] = {}
    for raw_root in paths if paths is not None else default_skill_paths():
        root = Path(raw_root)
        if not root.is_dir():
            continue
        candidates = [root] if (root / "SKILL.md").is_file() else sorted(root.iterdir())
        for directory in candidates:
            if not (directory / "SKILL.md").is_file():
                continue
            try:
                skill = load_skill(directory)
            except SkillLoadError as exc:
                if errors is not None:
                    errors.append(f"{directory}: {exc}")
                continue
            found.setdefault(skill.name, skill)
    return list(found.values())
