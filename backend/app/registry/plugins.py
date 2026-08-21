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
from app.registry import unregister

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
    """扫描插件目录：新增/更新插件，清理已删除文件。。
    """
    for path in sorted(p for p in plugins_dir.glob("*.py") if not p.name.startswith("_")):
        _load(path, plugins_dir)
    for module_name, info in list(_LOADED.items()):
        if not (plugins_dir / info.filename).exists():
            for name in info.node_names:
                unregister(name)
            del _LOADED[module_name]
            logger.info(
                "Removed stale plugin %s (types: %s)", info.filename, ", ".join(info.node_names)
            )


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
    """当前已加载（含加载失败）的插件状态，按文件名排序。"""
    return sorted(_LOADED.values(), key=lambda p: p.filename)


def _module_nodes(module: types.ModuleType) -> list[str]:
    """模块中被 @node 装饰的函数注册的节点名（按名字排序去重）。

    @node 注册时会把 NodeType 绑到函数对象上（``__node_type__``），
    直接从模块命名空间收集，不依赖执行前后的 REGISTRY 差集。

    按 ``__module__`` 过滤：只收集定义在本模块的函数——import 进来
    的已装饰函数（如复用内置节点）不属于本插件，否则重载时会把
    别处的注册误删且无法恢复。
    """
    return sorted(
        {
            node_type.name
            for value in vars(module).values()
            if (node_type := getattr(value, "__node_type__", None)) is not None
            and getattr(value, "__module__", None) == module.__name__
        }
    )


def _load(path: Path, plugins_dir: Path) -> None:
    """加载单个插件文件：先弹出旧注册；失败时本次加载全部撤销。

    REGISTRY 只反映文件当前的成功加载结果：坏更新会让本插件节点
    全部消失（error 记进 _LOADED），修复文件后重载即恢复。
    """
    module_name = f"{plugins_dir.name}.{path.stem}"
    prev = _LOADED.get(module_name)
    if prev is not None:
        for name in prev.node_names:
            unregister(name)

    module = None
    try:
        # exec_module 会校验 __pycache__ 的 .pyc：快速重写文件时 mtime
        # 相同会命中旧字节码（新内容不生效），执行期间禁用字节码缓存
        old_flag = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = old_flag
    except Exception as exc:
        # 失败：撤销失败模块已注册的部分节点，本文件注册为空集
        if module is not None:
            for name in _module_nodes(module):
                unregister(name)
        logger.error("Failed to reload plugin %s: %s", path.name, exc)
        _LOADED[module_name] = PluginInfo(
            filename=path.name,
            module=module_name,
            node_names=[],
            loaded_at=datetime.now(timezone.utc),
            error=str(exc),
        )
        return

    added = _module_nodes(module)
    _LOADED[module_name] = PluginInfo(
        filename=path.name,
        module=module_name,
        node_names=added,
        loaded_at=datetime.now(timezone.utc),
        error=None,
    )
    logger.info("Loaded plugin %s (registered %d: %s)", path.name, len(added), ", ".join(added))
