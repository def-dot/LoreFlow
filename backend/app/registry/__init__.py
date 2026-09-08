"""
结构化函数注册表 — 系统支持的节点类型目录。
"""

from .core import REGISTRY, NodeType, node_type, unregister
from .tools import TOOL_REGISTRY, tool
from .skills import SKILL_REGISTRY, SkillDef, discover_skills
from . import agent, base, default_tools, llm, other, rag, web, tools  # noqa: F401  # 导入即触发注册

__all__ = [
    "NodeType",
    "REGISTRY",
    "TOOL_REGISTRY",
    "SKILL_REGISTRY",
    "SkillDef",
    "node_type",
    "tool",
    "discover_skills",
    "unregister",
]
