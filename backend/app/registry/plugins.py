"""节点插件加载 — 扫描 settings.PLUGINS_DIR 下的 *.py 并导入。

- :func:`load_plugins` 启动时（lifespan）调用一次；坏文件跳过并记录
  error（本次加载全部撤销）

插件文件用 ``@node`` / ``@tool`` / ``@node_and_tool`` 装饰器定义函数：
导入即注册进 ``REGISTRY`` / ``TOOL_REGISTRY``。只扫描目录顶层的 *.py
（下划线前缀跳过），不支持子包。
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger
from app.registry import REGISTRY, TOOL_REGISTRY

logger = get_logger(__name__)
plugins_dir = Path(settings.PLUGINS_DIR)

@dataclass
class PluginInfo:
    filename: str
    module: str
    node_names: list[str]
    tool_names: list[str]
    loaded_at: datetime
    error: str | None = None


#: 已加载插件：module_name -> PluginInfo
_LOADED: dict[str, PluginInfo] = {}


def load_plugins() -> None:
    """整体重建：撤销全部插件注册后全量重扫。
    """
    for info in _LOADED.values():
        for name in info.node_names:
            REGISTRY.pop(name, None)
        for name in info.tool_names:
            TOOL_REGISTRY.pop(name, None)
    _LOADED.clear()
    for path in sorted(p for p in plugins_dir.glob("*.py") if not p.name.startswith("_")):
        _load(path)


def list_plugins() -> list[PluginInfo]:
    """当前已加载（含加载失败）的插件"""
    return sorted(_LOADED.values(), key=lambda p: p.filename)


def _load(path: Path) -> None:
    """加载单个插件文件（调用前注册表已被 load_plugins 清空）。

    不变量：node_names / tool_names 精确等于本文件最终注册——异常/冲突时为空集。
    跨注册表撞名（节点名占用已有工具名或反之）同样视为冲突。

    始终从源码编译执行，绕开 ``__pycache__``：热加载要求立即读到新内容，
    pyc 的秒级 mtime 校验会让同秒内重写的文件命中旧字节码。
    """
    module_name = f"{plugins_dir.name}.{path.stem}"
    existed_nodes = dict(REGISTRY)
    existed_tools = dict(TOOL_REGISTRY)
    new_nodes: set[str] = set()
    new_tools: set[str] = set()
    error: str | None = None

    def rollback() -> None:
        REGISTRY.clear()
        REGISTRY.update(existed_nodes)
        TOOL_REGISTRY.clear()
        TOOL_REGISTRY.update(existed_tools)

    try:
        source = path.read_text(encoding="utf-8")
        code = compile(source, str(path), "exec")
        module = types.ModuleType(module_name)
        module.__file__ = str(path)
        sys.modules[module_name] = module
        exec(code, module.__dict__)

        new_nodes = {
            k for k, v in REGISTRY.items()
            if existed_nodes.get(k) is not v
        }
        new_tools = {
            k for k, v in TOOL_REGISTRY.items()
            if existed_tools.get(k) is not v
        }
        conflicts = (
            (set(new_nodes) & set(existed_nodes))
            | (set(new_tools) & set(existed_tools))
            | (set(new_nodes) & set(existed_tools))
            | (set(new_tools) & set(existed_nodes))
        )
        if conflicts:
            names = ", ".join(sorted(conflicts))
            logger.error("Plugin %s conflicts on names: %s", path.name, names)
            error = f"名称冲突：{names} 已被内置或其他插件占用"
            rollback()
            new_nodes = set()
            new_tools = set()
        else:
            logger.info(
                "Loaded plugin %s (nodes: %d, tools: %d)",
                path.name, len(new_nodes), len(new_tools),
            )
    except Exception as exc:
        logger.error("Plugin %s error: %s", path.name, exc)
        error = str(exc)
        rollback()
        new_nodes = set()
        new_tools = set()

    _LOADED[module_name] = PluginInfo(
        filename=path.name,
        module=module_name,
        node_names=sorted(new_nodes),
        tool_names=sorted(new_tools),
        loaded_at=datetime.now(timezone.utc),
        error=error,
    )
