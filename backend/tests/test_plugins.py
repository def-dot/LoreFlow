"""插件加载 — load_plugins 扫描目录：新增/更新/清理删除/坏文件容错（启动与重载同路径）"""

import asyncio
import sys
from contextlib import suppress
from pathlib import Path

import pytest

import app.registry.plugins as plugin_loader
from app.core.config import settings
from app.registry import REGISTRY


@pytest.fixture
def isolated_registry():
    """隔离全局注册表、加载记录与 sys.modules，测试结束后原样恢复。"""
    registry_before = dict(REGISTRY)
    loaded_before = dict(plugin_loader._LOADED)
    modules_before = set(sys.modules)
    yield
    REGISTRY.clear()
    REGISTRY.update(registry_before)
    plugin_loader._LOADED.clear()
    plugin_loader._LOADED.update(loaded_before)
    for name in set(sys.modules) - modules_before:
        sys.modules.pop(name, None)


def _write(path: Path, body: str) -> None:
    path.write_text("from app.registry import node\n\n" + body, encoding="utf-8")


def test_load_plugins_scans_directory(monkeypatch, tmp_path, isolated_registry) -> None:
    _write(
        tmp_path / "notify.py",
        '@node(label="插件节点", description="目录扫描加载")\n'
        "async def dir_probe(ctx: dict) -> str:\n"
        '    return "ok"\n',
    )
    (tmp_path / "_skip.py").write_text("raise RuntimeError('不应被加载')\n", encoding="utf-8")
    monkeypatch.setattr(settings, "PLUGINS_DIR", tmp_path)

    plugin_loader.load_plugins()

    assert REGISTRY["dir_probe"].label == "插件节点"
    assert "_skip" not in REGISTRY  # 下划线前缀跳过
    info = next(
        p for p in plugin_loader.list_plugins() if p.module == f"{tmp_path.name}.notify"
    )  # conftest 也加载了真实目录里的 notify.py，按模块名区分
    assert info.node_names == ["dir_probe"] and info.error is None


def test_load_plugins_missing_dir(monkeypatch, tmp_path, isolated_registry) -> None:
    monkeypatch.setattr(settings, "PLUGINS_DIR", tmp_path / "no_such_dir")
    plugin_loader.load_plugins()  # 目录不存在只告警，不抛异常


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("条件在超时内未满足")


async def test_watch_plugins_auto_reloads(monkeypatch, tmp_path, isolated_registry) -> None:
    """后台轮询发现目录变化后自动重扫：放文件即生效，无需手动触发。"""
    _write(tmp_path / "a.py", '@node(label="A", description="")\nasync def a_probe(ctx: dict) -> str:\n    return "a"\n')
    monkeypatch.setattr(settings, "PLUGINS_DIR", tmp_path)
    monkeypatch.setattr(settings, "PLUGINS_POLL_SECONDS", 0.01)
    plugin_loader.load_plugins()

    task = asyncio.create_task(plugin_loader.watch_plugins())
    try:
        await asyncio.sleep(0)  # 让 watcher 先运行到记录初始签名，再写文件
        _write(tmp_path / "b.py", '@node(label="B", description="")\nasync def b_probe(ctx: dict) -> str:\n    return "b"\n')
        await _wait_until(lambda: "b_probe" in REGISTRY)
        assert "a_probe" in REGISTRY
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def test_sync_adds_new_file(monkeypatch, tmp_path, isolated_registry) -> None:
    _write(tmp_path / "a.py", '@node(label="A", description="")\nasync def a_probe(ctx: dict) -> str:\n    return "a"\n')
    monkeypatch.setattr(settings, "PLUGINS_DIR", tmp_path)
    plugin_loader.load_plugins()

    _write(tmp_path / "b.py", '@node(label="B", description="")\nasync def b_probe(ctx: dict) -> str:\n    return "b"\n')
    plugin_loader.load_plugins()

    assert "a_probe" in REGISTRY and "b_probe" in REGISTRY
    assert {p.filename for p in plugin_loader.list_plugins()} == {"a.py", "b.py"}


def test_sync_removes_deleted_file(monkeypatch, tmp_path, isolated_registry) -> None:
    path = tmp_path / "a.py"
    _write(path, '@node(label="A", description="")\nasync def a_probe(ctx: dict) -> str:\n    return "a"\n')
    monkeypatch.setattr(settings, "PLUGINS_DIR", tmp_path)
    plugin_loader.load_plugins()

    path.unlink()
    plugin_loader.load_plugins()

    assert "a_probe" not in REGISTRY  # 僵尸类型被清理
    assert plugin_loader.list_plugins() == []


def test_sync_broken_new_file_registers_nothing(monkeypatch, tmp_path, isolated_registry) -> None:
    """新文件注册一半后抛错：已注册的部分被清除，其余插件不受影响。"""
    _write(tmp_path / "a.py", '@node(label="A", description="")\nasync def a_probe(ctx: dict) -> str:\n    return "a"\n')
    monkeypatch.setattr(settings, "PLUGINS_DIR", tmp_path)
    plugin_loader.load_plugins()

    _write(
        tmp_path / "broken.py",
        '@node(label="半成品", description="")\nasync def broken_probe(ctx: dict) -> str:\n    return "b"\n'
        'raise RuntimeError("boom")\n',
    )
    plugin_loader.load_plugins()

    assert "a_probe" in REGISTRY
    assert "broken_probe" not in REGISTRY  # 部分注册被清除
    info = next(p for p in plugin_loader.list_plugins() if p.filename == "broken.py")
    assert info.node_names == [] and "boom" in info.error


def test_plugin_importing_builtin_node(monkeypatch, tmp_path, isolated_registry) -> None:
    """插件 import 内置节点复用：不算本插件注册，重载不误删内置。"""
    _write(
        tmp_path / "wrap.py",
        "from app.registry.nodes import cfg_fetch\n\n"
        '@node(label="包装", description="复用内置抓取")\n'
        "async def wrap_probe(ctx: dict) -> str:\n"
        '    data = await cfg_fetch(ctx)\n'
        '    return data["title"]\n',
    )
    monkeypatch.setattr(settings, "PLUGINS_DIR", tmp_path)
    plugin_loader.load_plugins()

    assert "cfg_fetch" in REGISTRY
    assert REGISTRY["wrap_probe"].label == "包装"
    info = next(p for p in plugin_loader.list_plugins() if p.module == f"{tmp_path.name}.wrap")
    assert info.node_names == ["wrap_probe"]  # cfg_fetch 不属于本插件

    plugin_loader.load_plugins()  # 重载后内置节点必须仍在

    assert "cfg_fetch" in REGISTRY
    assert REGISTRY["wrap_probe"].label == "包装"


def test_sync_updates_existing_file(monkeypatch, tmp_path, isolated_registry) -> None:
    """同一文件内容变化：新增节点注册进来，删除节点消失。"""
    path = tmp_path / "a.py"
    _write(path, '@node(label="A", description="")\nasync def a_probe(ctx: dict) -> str:\n    return "a"\n')
    monkeypatch.setattr(settings, "PLUGINS_DIR", tmp_path)
    plugin_loader.load_plugins()
    assert "a_probe" in REGISTRY

    # 更新：删除 a_probe，新增 b_probe
    _write(path, '@node(label="B", description="")\nasync def b_probe(ctx: dict) -> str:\n    return "b"\n')
    plugin_loader.load_plugins()

    assert "a_probe" not in REGISTRY
    assert REGISTRY["b_probe"].label == "B"
    info = next(p for p in plugin_loader.list_plugins() if p.module == f"{tmp_path.name}.a")
    assert info.node_names == ["b_probe"] and info.error is None


def test_sync_broken_update_clears_nodes(monkeypatch, tmp_path, isolated_registry) -> None:
    """已加载文件被坏版本覆盖：本文件节点全部消失并记录 error，修复后重载恢复。"""
    path = tmp_path / "a.py"
    good = '@node(label="A", description="")\nasync def a_probe(ctx: dict) -> str:\n    return "a"\n'
    _write(path, good)
    monkeypatch.setattr(settings, "PLUGINS_DIR", tmp_path)
    plugin_loader.load_plugins()

    _write(path, "raise RuntimeError('boom')\n")
    plugin_loader.load_plugins()

    assert "a_probe" not in REGISTRY  # 坏更新后节点消失，fail-obvious
    info = next(p for p in plugin_loader.list_plugins() if p.module == f"{tmp_path.name}.a")
    assert info.node_names == [] and "boom" in info.error

    _write(path, good)
    plugin_loader.load_plugins()

    assert REGISTRY["a_probe"].label == "A"  # 修复后重载恢复
    info = next(p for p in plugin_loader.list_plugins() if p.module == f"{tmp_path.name}.a")
    assert info.node_names == ["a_probe"] and info.error is None
