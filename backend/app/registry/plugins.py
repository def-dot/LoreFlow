"""节点插件加载 — 扫描 settings.PLUGINS_DIR 下的 *.py 并导入。

:func:`load_plugins` 在启动时（lifespan）与重载端点处调用：
新增/更新插件、清理已删除文件；坏文件跳过并记录 error（旧注册回滚）。

插件文件只需用 ``@node`` 装饰器定义函数：导入即注册进 ``REGISTRY``。
只扫描目录顶层的 *.py（下划线前缀跳过），不支持子包。
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger
from app.registry import REGISTRY, NodeType

logger = get_logger(__name__)


@dataclass
class PluginInfo:
    filename: str
    module: str
    node_names: list[str]
    loaded_at: datetime
    error: str | None = None


#: 已加载插件：module_name -> PluginInfo
_LOADED: dict[str, PluginInfo] = {}


def load_plugins() -> None:
    """扫描插件目录：新增/更新插件，清理已删除文件。

    启动时（lifespan）与重载端点均调用；坏文件跳过并记录 error，
    不影响其余插件（启动期 _LOADED 为空，清理是空转）。
    """
    plugins_dir = Path(settings.PLUGINS_DIR)
    if not plugins_dir.is_dir():
        logger.warning("Plugin directory %s not found, skipping", plugins_dir)
        return
    for path in sorted(p for p in plugins_dir.glob("*.py") if not p.name.startswith("_")):
        _load(path, plugins_dir)
    for module_name, info in list(_LOADED.items()):
        if not (plugins_dir / info.filename).exists():
            for name in info.node_names:
                REGISTRY.pop(name, None)
            del _LOADED[module_name]
            logger.info(
                "Removed stale plugin %s (types: %s)", info.filename, ", ".join(info.node_names)
            )


def list_plugins() -> list[PluginInfo]:
    """当前已加载（含加载失败）的插件状态，按文件名排序。"""
    return sorted(_LOADED.values(), key=lambda p: p.filename)


def _load(path: Path, plugins_dir: Path) -> None:
    """加载单个插件文件：失败时回滚旧模块与旧注册，error 记进 _LOADED。"""
    module_name = f"{plugins_dir.name}.{path.stem}"
    prev = _LOADED.get(module_name)
    old_module = sys.modules.get(module_name)

    popped: dict[str, NodeType] = {}
    if prev is not None:
        for name in prev.node_names:
            if (node_type := REGISTRY.pop(name, None)) is not None:
                popped[name] = node_type
    known = set(REGISTRY)

    try:
        # 直接读源码执行，不走 SourceFileLoader 的 pyc 缓存：
        # 文件快速重写时 mtime 相同会命中旧字节码，导致新内容不生效
        source = path.read_text(encoding="utf-8")
        module = types.ModuleType(module_name)
        module.__file__ = str(path)
        sys.modules[module_name] = module
        exec(compile(source, str(path), "exec"), module.__dict__)
    except Exception as exc:
        # 回滚：恢复旧模块对象与旧注册，清除失败模块已注册的新名字
        if old_module is not None:
            sys.modules[module_name] = old_module
        else:
            sys.modules.pop(module_name, None)
        for name in set(REGISTRY) - known:
            REGISTRY.pop(name, None)
        REGISTRY.update(popped)
        logger.error("Failed to reload plugin %s: %s", path.name, exc)
        _LOADED[module_name] = PluginInfo(
            filename=prev.filename if prev is not None else path.name,
            module=module_name,
            node_names=prev.node_names if prev is not None else [],
            loaded_at=prev.loaded_at if prev is not None else datetime.now(timezone.utc),
            error=str(exc),
        )
        return

    added = sorted(set(REGISTRY) - known)
    _LOADED[module_name] = PluginInfo(
        filename=path.name,
        module=module_name,
        node_names=added,
        loaded_at=datetime.now(timezone.utc),
        error=None,
    )
    logger.info("Loaded plugin %s (registered %d: %s)", path.name, len(added), ", ".join(added))
