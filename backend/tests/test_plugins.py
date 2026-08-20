"""插件加载 — load_plugins 扫描目录：新增/更新/清理删除/坏文件容错（启动与重载同路径）"""

import sys
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


def test_sync_broken_new_file_rolls_back(monkeypatch, tmp_path, isolated_registry) -> None:
    """新文件注册一半后抛错：已注册的名字回滚，其余插件不受影响。"""
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
    assert "broken_probe" not in REGISTRY  # 部分注册被回滚
    info = next(p for p in plugin_loader.list_plugins() if p.filename == "broken.py")
    assert info.node_names == [] and "boom" in info.error


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


def test_sync_broken_update_keeps_old_version(monkeypatch, tmp_path, isolated_registry) -> None:
    """已加载文件被坏版本覆盖：旧注册回滚保留，状态记录 error。"""
    path = tmp_path / "a.py"
    _write(path, '@node(label="A", description="")\nasync def a_probe(ctx: dict) -> str:\n    return "a"\n')
    monkeypatch.setattr(settings, "PLUGINS_DIR", tmp_path)
    plugin_loader.load_plugins()

    _write(path, "raise RuntimeError('boom')\n")
    plugin_loader.load_plugins()

    assert REGISTRY["a_probe"].label == "A"  # 旧版本仍然生效
    info = next(p for p in plugin_loader.list_plugins() if p.filename == "a.py")
    assert info.node_names == ["a_probe"] and "boom" in info.error
