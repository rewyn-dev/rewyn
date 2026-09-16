"""Skills: SKILL.md loading, discovery, registry and lifecycle."""

from rewyn.skills.discovery import default_skill_paths, discover_skills
from rewyn.skills.lifecycle import SkillManager
from rewyn.skills.loader import SkillLoadError, load_skill, parse_skill_markdown
from rewyn.skills.registry import SkillRegistry, as_skill
from rewyn.skills.skill import Skill, skill

__all__ = [
    "Skill",
    "SkillLoadError",
    "SkillManager",
    "SkillRegistry",
    "as_skill",
    "default_skill_paths",
    "discover_skills",
    "load_skill",
    "parse_skill_markdown",
    "skill",
]
