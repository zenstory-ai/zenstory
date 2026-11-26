"""
Skills system for the AI writing assistant.

Skills follow the standard Agent Skills format
(SKILL.md + references/ + assets/ + examples/ + templates/).
They are disclosed progressively: the system prompt lists name + description + trigger hints (L1),
the `load_skill` tool returns instructions (L2), and `read_skill_resource` returns
one resource file (L3). Skill code is never executed.
"""

from .active_skills import ActiveSkill, load_active_skills, resolve_selected_skills
from .context_injector import SkillContextInjector, get_skill_context_injector
from .package import ParsedSkill, SkillPackageError, build_skill_zip, parse_skill_md, read_skill_zip
from .replay import (
    SKILL_REPLAY_TOKEN_BUDGET,
    index_active_skills,
    rehydrate_skill_tool_result,
)

__all__ = [
    "SKILL_REPLAY_TOKEN_BUDGET",
    "ActiveSkill",
    "ParsedSkill",
    "SkillContextInjector",
    "SkillPackageError",
    "build_skill_zip",
    "get_skill_context_injector",
    "index_active_skills",
    "load_active_skills",
    "parse_skill_md",
    "read_skill_zip",
    "rehydrate_skill_tool_result",
    "resolve_selected_skills",
]
