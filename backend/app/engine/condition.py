"""
条件表达式 — YAML ``condition`` 声明的解析与求值。

语法（对标 Argo ``when`` 的内嵌条件）::

    condition: intent == chat          # 等值 / 不等（== !=）
    condition: score >= 0.8            # 大小比较（> >= < <=）
    condition: intent in [chat, rag]   # 成员（in / not in，值为列表）
    condition: merge                   # 裸键真值（视图值非空即真）
    condition: not flag                # 取反
    condition: $router.intent == rag   # $ 引用前缀（与 inputs 接线同拼法，等价于省略 $）

- 键在节点视图上取值（共享 ctx + ``inputs`` 接线本地键；loop 额外注入
  ``iteration``），支持 ``a.b.c`` 点路径下钻 dict 字段。加载期按
  「params ∪ 节点名 ∪ inputs 本地键」核对根键存在 —— 拼错键在载入时
  报，不等到运行。
- 值为标量字面量：裸词按字符串（``chat``）、数字/true/false/null 按
  字面量、带空格的字符串加引号；``in`` 的值是 ``[a, b]`` 列表。数字
  与字符串不隐式转换（``1 == "1"`` 为 False）。
- 求值异常（类型不可比等）按 False 处理 —— 与执行器「条件异常 → 跳过」
  的既有语义一致。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .node import ConditionFunc

#: 表达式 = [not] 键 [操作符 值]。键不含空白/比较符字符（支持中文与
#: ``.`` 字段路径）；值至少一个非空字符（``intent ==`` 缺值则整体不匹配）。
_EXPR_RE = re.compile(
    r"^\s*(?P<neg>not\s+)?(?P<key>[^\s=!<>]+)"
    r"(?:\s*(?P<op>not\s+in|==|!=|>=|<=|>|<|in)\s*(?P<value>\S.*?))?\s*$"
)


def _parse_scalar(raw: str) -> Any:
    """单个值字面量：引号字符串 / true/false/null / 数字 / 裸词字符串。"""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in ("null", "none"):
        return None
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            continue
    return raw


def _parse_value(raw: str) -> Any:
    """操作符右侧的值：``[a, b]`` 列表（in/not in 用）或标量。"""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        return [_parse_scalar(p.strip()) for p in inner.split(",") if p.strip()] if inner else []
    return _parse_scalar(raw)


def _parse(expr: str) -> tuple[bool, str, str | None, Any]:
    """表达式 → ``(取反, 键, 操作符或 None, 期望值)``；语法错抛中文 ValueError。"""
    m = _EXPR_RE.match(expr)
    if not m:
        raise ValueError(
            f"条件表达式 {expr!r} 无法解析（写法如 ``intent == chat``、``merge``、``not flag``）"
        )
    value_raw = m.group("value")
    return (
        m.group("neg") is not None,
        m.group("key").removeprefix("$"),  # $ 引用前缀（与 inputs 接线同拼法），等价于省略
        m.group("op"),
        _parse_value(value_raw) if value_raw is not None else None,
    )


def _compare(actual: Any, op: str, expected: Any) -> bool:
    if op == "==":
        return actual == expected
    if op == "!=":
        return actual != expected
    if op == "in":
        return isinstance(expected, (list, tuple, set)) and actual in expected
    if op == "not in":
        return isinstance(expected, (list, tuple, set)) and actual not in expected
    # 大小比较：类型不可比（str vs int 等）按 False，不抛异常
    try:
        if op == ">":
            return actual > expected
        if op == ">=":
            return actual >= expected
        if op == "<":
            return actual < expected
        return actual <= expected
    except TypeError:
        return False


def compile_condition(expr: str) -> ConditionFunc:
    """表达式字符串 → ``(视图) -> bool`` 谓词（解析一次，循环内重复求值）。"""
    neg, key, op, expected = _parse(expr)

    def cond(ctx: dict[str, Any]) -> bool:
        actual = ctx
        for part in key.split("."):  # a.b.c 逐段下钻 dict，缺段/非 dict → None
            if not isinstance(actual, Mapping) or part not in actual:
                actual = None
                break
            actual = actual[part]
        result = _compare(actual, op, expected) if op else bool(actual)
        return not result if neg else result

    return cond
