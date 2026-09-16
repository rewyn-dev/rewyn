"""Load skills from the standard ``SKILL.md`` layout::

skills/
└── financial-analysis/
    ├── SKILL.md          # YAML frontmatter + markdown instructions
    ├── scripts/
    └── resources/
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from rewyn.core.types import RewynError
from rewyn.skills.skill import Skill

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


class SkillLoadError(RewynError):
    pass


def parse_skill_markdown(text: str) -> tuple[dict[str, Any], str]:
    """Split ``SKILL.md`` into (frontmatter, body)."""
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text.strip()
    raw, body = match.group(1), match.group(2)
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise SkillLoadError(f"invalid SKILL.md frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillLoadError("SKILL.md frontmatter must be a mapping")
    return data, body.strip()


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v) for v in value]


def _files_under(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(str(p.relative_to(directory.parent)) for p in directory.rglob("*") if p.is_file())


def load_skill(path: Path | str) -> Skill:
    """Load a skill from a directory containing ``SKILL.md`` (or the file itself)."""
    location = Path(path)
    skill_file = location / "SKILL.md" if location.is_dir() else location
    if not skill_file.is_file():
        raise SkillLoadError(f"no SKILL.md found at {location}")
    directory = skill_file.parent
    frontmatter, body = parse_skill_markdown(skill_file.read_text(encoding="utf-8"))
    name = str(frontmatter.get("name") or directory.name)
    description = frontmatter.get("description")
    if not description:
        raise SkillLoadError(f"skill {name!r} is missing a description in SKILL.md frontmatter")
    known = {
        "name",
        "description",
        "version",
        "license",
        "allowed-tools",
        "allowed_tools",
        "permissions",
        "dependencies",
        "metadata",
    }
    extra_metadata = {k: v for k, v in frontmatter.items() if k not in known}
    metadata = dict(frontmatter.get("metadata") or {})
    metadata.update(extra_metadata)
    return Skill(
        name=name,
        description=str(description).strip(),
        instructions=body,
        version=str(frontmatter.get("version", "1")),
        path=directory,
        resources=_files_under(directory / "resources"),
        scripts=_files_under(directory / "scripts"),
        allowed_tools=_as_list(frontmatter.get("allowed-tools", frontmatter.get("allowed_tools"))),
        permissions=_as_list(frontmatter.get("permissions")),
        dependencies=_as_list(frontmatter.get("dependencies")),
        license=str(frontmatter["license"]) if frontmatter.get("license") else None,
        metadata=metadata,
    )
