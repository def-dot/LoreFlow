"""节点插件加载 — 扫描 settings.PLUGINS_DIR 下的 *.py 并导入。

- :func:`load_plugins` 启动时（lifespan）调用一次；坏文件跳过并记录
  error（本次加载全部撤销）
- :func:`watch_plugins` 后台轮询目录签名，变化时自动重扫——多 worker
  各自轮询、最终一致，插件发布 = 放文件，无需重启或手动重载

插件文件只需用 ``@node`` 装饰器定义函数：导入即注册进 ``REGISTRY``。
只扫描目录顶层的 *.py（下划线前缀跳过），不支持子包。
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger
from app.registry import REGISTRY, NodeType, unregister

logger = get_logger(__name__)
plugins_dir = Path(settings.PLUGINS_DIR)

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
    """整体重建：撤销全部插件注册后全量重扫。

    删除的文件自然消失（不再被扫描），坏文件跳过并记录 error。
    同步执行于事件循环中，重建期间无请求穿插（原子可见）。

    节点名冲突（与内置或其他插件重复）时，冲突插件整体判失败：
    冲突名回滚到原归属，其余注册撤销，error 记录冲突详情。
    """
    for info in _LOADED.values():
        for name in info.node_names:
            unregister(name)
    _LOADED.clear()
    # wipe 后 REGISTRY 里只剩内置；taken 追踪每个名字的当前归属（用于冲突回滚）
    for path in sorted(p for p in plugins_dir.glob("*.py") if not p.name.startswith("_")):
        _load(path)


async def watch_plugins() -> None:
    """后台轮询插件目录：签名变化时自动重扫（供 lifespan 创建任务）。
    """
    last = _dir_signature(plugins_dir)
    while True:
        await asyncio.sleep(settings.PLUGINS_POLL_SECONDS)
        signature = _dir_signature(plugins_dir)
        if signature != last:
            last = signature
            load_plugins()


def _dir_signature(plugins_dir: Path) -> tuple[tuple[str, int, int], ...]:
    """目录内容签名：(文件名, mtime_ns, 大小)；任何增删改都会改变签名。"""
    return tuple(
        (p.name, p.stat().st_mtime_ns, p.stat().st_size)
        for p in sorted(p for p in plugins_dir.glob("*.py") if not p.name.startswith("_"))
    )


def list_plugins() -> list[PluginInfo]:
    """当前已加载（含加载失败）的插件"""
    return sorted(_LOADED.values(), key=lambda p: p.filename)


def _load(path: Path) -> None:
    """加载单个插件文件（调用前注册表已被 load_plugins 清空）。
    """
    module_name = f"{plugins_dir.name}.{path.stem}"
    new_nodes = set()
    existed_nodes = dict(REGISTRY)
    error = None
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        new_nodes = {
            node_type.name
            for value in vars(module).values()
            if (node_type := getattr(value, "__node_type__", None)) is not None
            and getattr(value, "__module__", None) == module.__name__
        }

        conflicts = set(new_nodes) & set(existed_nodes)
        if conflicts:
            logger.error("Plugin %s conflicts on nodes: %s", path.name, ", ".join(conflicts))
            error = f"节点冲突：{', '.join(conflicts)} 已被内置节点或其他插件占用"

            for node_name in new_nodes:
                unregister(node_name)

            for node_name in conflicts:
                REGISTRY[node_name] = existed_nodes[node_name]
        else:
            logger.info("Loaded plugin %s (registered %d: %s)", path.name, len(new_nodes), ", ".join(new_nodes))
    except Exception as exc:
        logger.error("Plugin %s error: %s", path.name)
        error = str(exc)

    _LOADED[module_name] = PluginInfo(
        filename=path.name,
        module=module_name,
        node_names=list(new_nodes),
        loaded_at=datetime.now(timezone.utc),
        error=error,
    )
 