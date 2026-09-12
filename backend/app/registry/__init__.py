"""
结构化函数注册表 — 系统支持的节点类型目录。
"""

from .node_type import REGISTRY, NodeType, node_type, unregister
from .tool import TOOL_REGISTRY, tool, execute_tool_call
from .skills import SKILL_REGISTRY, SkillDef, discover_skills

# 导入即触发注册
from . import plugins  # noqa: F401
from .node_types import base, llm, rag, web, agent, other, tool_executor  # noqa: F401
from .tools import sandbox, skills as tool_skills, web as tool_web  # noqa: F401

__all__ = [
    "NodeType",
    "REGISTRY",
    "TOOL_REGISTRY",
    "SKILL_REGISTRY",
    "SkillDef",
    "node_type",
    "tool",
    "execute_tool_call",
    "discover_skills",
    "unregister",
]
