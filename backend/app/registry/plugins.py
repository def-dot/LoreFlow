"""节点插件加载 — 扫描 settings.PLUGINS_DIR 下的 *.py 并导入。

- :func:`load_plugins` 启动期全量加载（lifespan 调用一次）：任何文件
  导入失败直接抛出，配置错误尽早暴露。
- :func:`sync_plugins` 运行期重扫（reload 端点触发）：单文件容错 —
  坏文件跳过并记录 error（旧注册回滚），已删除文件清理其注册的类型。

插件文件只需用 ``@node`` 装饰器定义函数：导入即注册进 ``REGISTRY``，
YAML 的 ``type:``/``condition:`` 与 /node-types API 随即可用，前端无需改动。
只扫描目录顶层的 *.py（下划线前缀与 __init__.py 跳过），不支持子包。
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger
from app.registry import REGISTRY, NodeType

logger = get_logger(__name__)


@dataclass
class PluginInfo:
    """一个已加载插件文件的状态。"""

    filename: str
    module: str
    node_names: list[str]
    loaded_at: datetime
    error: str | None = None


#: 已加载插件：module_name -> PluginInfo。sync_plugins 据 filename
#: 清理已删除文件注册的僵尸类型。
_LOADED: dict[str, PluginInfo] = {}


def load_plugins() -> None:
    """启动期全量加载（lifespan 调用一次），导入失败直接抛出。"""
    plugins_dir = Path(settings.PLUGINS_DIR)
    if not plugins_dir.is_dir():
        logger.warning("Plugin directory %s not found, skipping", plugins_dir)
        return
    for path in sorted(plugins_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        _load_file(path, plugins_dir)


def sync_plugins() -> None:
    """运行期重扫（reload 端点触发）：新增/更新插件，清理已删除文件。

    单文件容错：坏文件跳过并记录 error，旧注册回滚，不影响其余插件。
    """
    plugins_dir = Path(settings.PLUGINS_DIR)
    if not plugins_dir.is_dir():
        logger.warning("Plugin directory %s not found, skipping", plugins_dir)
        return
    for path in sorted(plugins_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        _reload_file(path, plugins_dir)

    # 清理已删除文件的注册（插件类型 builtin=True，unregister 拒撤，直接 pop）
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


def _load_file(path: Path, plugins_dir: Path) -> None:
    """启动期严格加载：失败直接抛出。"""
    module_name = f"{plugins_dir.name}.{path.stem}"
    known = set(REGISTRY)
    _exec_file(module_name, path)
    _record(module_name, path.name, known)


def _reload_file(path: Path, plugins_dir: Path) -> None:
    """运行期重载：先弹出旧注册，失败时回滚旧模块与旧注册。"""
    module_name = f"{plugins_dir.name}.{path.stem}"
    prev = _LOADED.get(module_name)
    old_module = sys.modules.get(module_name)

    popped: dict[str, NodeType] = {}
    if prev is not None:
        popped = {name: REGISTRY.pop(name, None) for name in prev.node_names}
        popped = {name: t for name, t in popped.items() if t is not None}

    known = set(REGISTRY)
    try:
        _exec_file(module_name, path)
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
        if prev is not None:
            _LOADED[module_name] = PluginInfo(
                filename=prev.filename,
                module=module_name,
                node_names=prev.node_names,
                loaded_at=prev.loaded_at,
                error=str(exc),
            )
        else:
            _LOADED[module_name] = PluginInfo(
                filename=path.name,
                module=module_name,
                node_names=[],
                loaded_at=datetime.now(timezone.utc),
                error=str(exc),
            )
        return
    _record(module_name, path.name, known)


def _exec_file(module_name: str, path: Path) -> None:
    """按路径导入插件文件（先写入 sys.modules，供插件内部相对引用）。"""
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)


def _record(module_name: str, filename: str, known: set[str]) -> None:
    """记录本次加载新增的注册名（重载场景：旧名已先弹出，即完整集合）。"""
    added = sorted(set(REGISTRY) - known)
    _LOADED[module_name] = PluginInfo(
        filename=filename,
        module=module_name,
        node_names=added,
        loaded_at=datetime.now(timezone.utc),
        error=None,
    )
    logger.info("Loaded plugin %s (registered %d: %s)", filename, len(added), ", ".join(added))
