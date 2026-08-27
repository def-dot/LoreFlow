"""YAML ``inputs`` 数据流接线 — 校验与注入视图的单元测试（不经 HTTP）。

值语义："$键" = 引用（去前缀取 ctx 键，校验存在 + 上游链）；其余
（裸字符串/数字/布尔等）= 字面量原样注入。
"""

from typing import Any

import pytest

from app.engine.declarative import load_dag
from app.engine.validate import validate_config
from app.registry.core import node, unregister


@pytest.fixture(autouse=True)
def probe():
    """临时注册探针节点：回显 ctx['document']。"""

    @node(label="接线探针", description="回显 ctx['document']", name="wire_probe")
    async def wire_probe(ctx: dict[str, Any]) -> dict[str, Any]:
        return {"seen_document": ctx.get("document")}

    yield
    unregister("wire_probe")


CFG: dict[str, Any] = {
    "name": "wiring_demo",
    "inputs": {"document": {"required": False, "default": "参数原件"}},
    "nodes": {
        "生产者": {"type": "wire_probe"},
        "消费者": {
            "type": "wire_probe",
            "depends_on": ["生产者"],
            "inputs": {"document": "$生产者"},
        },
        "旁观察者": {"type": "wire_probe", "depends_on": ["消费者"]},
    },
}


async def test_wiring_remaps_and_shadows_param() -> None:
    """接线键重映射上游输出，且可遮蔽同名参数（仅本节点视图）。"""
    results = await load_dag(CFG).run()
    producer_out = results["生产者"].output
    assert results["消费者"].output == {"seen_document": producer_out}


async def test_wiring_does_not_leak_to_shared_ctx() -> None:
    """接线只改节点视图：未接线的后续节点看到的仍是参数原件。"""
    results = await load_dag(CFG).run()
    assert results["旁观察者"].output == {"seen_document": "参数原件"}


async def test_wiring_feeds_condition_true() -> None:
    """条件在接线视图上判定：wired 键有真值 → 节点执行。"""
    cfg = {
        "name": "wiring_condition_true",
        "nodes": {
            "生产者": {"type": "wire_probe"},
            "走条件": {
                "type": "wire_probe",
                "depends_on": ["生产者"],
                "condition": "flag",          # 裸键真值
                "inputs": {"flag": "$生产者"},  # 生产者输出是非空 dict → True
            },
        },
    }
    results = await load_dag(cfg).run()
    assert results["走条件"].status.value == "completed"


async def test_wiring_feeds_condition_false_skips() -> None:
    """wired 键为空（inputs 默认空串）→ 条件 False → 节点跳过。"""
    cfg = {
        "name": "wiring_condition_false",
        "inputs": {"flag": {"required": False, "default": ""}},
        "nodes": {
            "生产者": {"type": "wire_probe"},
            "跳过条件": {
                "type": "wire_probe",
                "depends_on": ["生产者"],
                "condition": "flag",
                "inputs": {"flag": "$flag"},  # 流水线 inputs flag = "" → False
            },
        },
    }
    results = await load_dag(cfg).run()
    assert results["跳过条件"].status.value == "skipped"


async def test_wiring_string_literal() -> None:
    """不带 @ 的裸字符串 = 字面量：原样注入视图，不当作引用校验。"""
    cfg = {"nodes": {"节点": {"type": "wire_probe", "inputs": {"document": "固定文本"}}}}
    results = await load_dag(cfg).run()
    assert results["节点"].output == {"seen_document": "固定文本"}


async def test_wiring_non_string_literal() -> None:
    """非字符串值 = 字面量原样注入视图。"""
    cfg = {"nodes": {"节点": {"type": "wire_probe", "inputs": {"document": 5}}}}
    results = await load_dag(cfg).run()
    assert results["节点"].output == {"seen_document": 5}


def test_validate_unknown_source() -> None:
    """@ 引用的来源既不是节点名也不是参数键 → 加载期报错。"""
    cfg = {"nodes": {"甲": {"type": "wire_probe"}, "乙": {"type": "wire_probe", "inputs": {"x": "$不存在"}}}}
    assert any("不是节点名或参数键" in e for e in validate_config(cfg))


def test_validate_source_must_be_upstream() -> None:
    """来源节点不在 depends_on 上游链 → 报错（数据只能来自已声明的上游）。"""
    cfg = {
        "nodes": {
            "甲": {"type": "wire_probe"},
            "乙": {"type": "wire_probe", "inputs": {"x": "$甲"}},  # 未声明依赖
        }
    }
    assert any("不在 depends_on 上游链中" in e for e in validate_config(cfg))


def test_validate_transitive_upstream_allowed() -> None:
    """隔代上游（依赖的依赖）可以接线，不要求直接依赖。"""
    cfg = {
        "nodes": {
            "甲": {"type": "wire_probe"},
            "乙": {"type": "wire_probe", "depends_on": ["甲"]},
            "丙": {"type": "wire_probe", "depends_on": ["乙"], "inputs": {"x": "$甲"}},
        }
    }
    assert not any("inputs" in e for e in validate_config(cfg))


def test_validate_literal_skips_source_check() -> None:
    """字面量（裸字符串或非字符串）不要求来源存在；@ 引用才校验。"""
    cfg = {"nodes": {"甲": {"type": "wire_probe", "inputs": {"a": "任意裸字符串", "b": True}}}}
    assert not any("inputs" in e for e in validate_config(cfg))


def test_validate_human_accepts_inputs() -> None:
    """human 节点同样支持 inputs（其条件在接线视图上判定）。"""
    cfg = {
        "nodes": {
            "甲": {"type": "wire_probe"},
            "审核": {"kind": "human", "depends_on": ["甲"], "inputs": {"x": "$甲"}},
        }
    }
    assert not any("不支持的字段" in e for e in validate_config(cfg))


@pytest.mark.parametrize("bad", [[1, 2], "document", 5])
def test_validate_malformed_inputs(bad: Any) -> None:
    """inputs 非映射（列表/字符串/数字）→ 报错。"""
    cfg = {"nodes": {"甲": {"type": "wire_probe", "inputs": bad}}}
    assert any("inputs 必须是" in e for e in validate_config(cfg))


def test_validate_empty_inputs_allowed() -> None:
    """inputs: {}（空映射）合法 —— 等价于不接线，视图即共享 ctx。"""
    cfg = {"nodes": {"甲": {"type": "wire_probe", "inputs": {}}}}
    assert not any("inputs" in e for e in validate_config(cfg))
