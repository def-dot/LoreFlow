"""条件表达式 — YAML ``condition`` 声明的解析与求值。

语法（对标 Argo ``when`` 的内嵌条件；键必须带 ``$`` 引用前缀——与
inputs 接线同一拼法，``$`` 开头 = 引用上下文）::

    condition: $intent == chat          # 等值 / 不等（== !=）
    condition: $score >= 0.8            # 大小比较（> >= < <=）
    condition: $intent in chat,rag      # 成员（in / not in，逗号分隔）
    condition: $merge                   # 裸键真值（视图值非空即真）
    condition: not $flag                # 取反
    condition: $router.intent == rag    # 点路径下钻上游输出字段
    condition: $score >= 0.8 and $intent == chat   # and（优先级高于 or）
    condition: $a == x or $b == y                   # or

- 键在节点视图上取值（共享 ctx + ``inputs`` 接线本地键；loop 额外注入
  ``iteration``），支持 ``a.b.c`` 点路径下钻 dict 字段。引用键在加载期
  由 ``_validate_node_condition`` 校验来源（参数键 / 上游依赖节点）。
- 值为标量字面量：裸词按字符串（``chat``）、数字/true/false/null 按
  字面量、带空格的字符串加引号；``in``/``not in`` 的值为逗号分隔列表。数字
  与字符串不隐式转换（``1 == "1"`` 为 False）。
- 求值异常（类型不可比等）按 False 处理 —— 与执行器「条件异常 → 跳过」
  的既有语义一致。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import lru_cache
from typing import Any, NamedTuple

from .node import ConditionFunc

#: 表达式 = [not] $键 [操作符 值]。键以 ``$`` 开头（引用上下文，与 inputs
#: 接线同拼法），不含空白/比较符字符（支持中文与 ``.`` 字段路径）；值至少
#: 一个非空字符（``$intent ==`` 缺值则整体不匹配）。
_EXPR_RE = re.compile(
    r"^\s*(?P<neg>not\s+)?\$(?P<key>[^\s=!<>]+)"
    r"(?:\s*(?P<op>not\s+in|==|!=|>=|<=|>|<|in)\s*(?P<value>\S.*?))?\s*$"
)

# 按逻辑运算符拆分，仅在运算符后跟 $/not $ 时生效（预编译）。
_SPLIT_RE: dict[str, re.Pattern[str]] = {
    "or": re.compile(r"\s+or\s+(?=\$|not\s+\$)"),
    "and": re.compile(r"\s+and\s+(?=\$|not\s+\$)"),
}


class Atom(NamedTuple):
    """单条原子条件的解析结果。"""

    neg: bool
    key: str
    op: str | None
    expected: Any


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


def _parse_atom(raw: str) -> Atom:
    """单条原子表达式；语法错抛中文 ValueError。"""
    m = _EXPR_RE.match(raw)
    if not m:
        raise ValueError(
            f"条件表达式 {raw!r} 无法解析（写法如 ``$intent == chat``、``$merge``、``not $flag``）"
        )

    value_raw = m.group("value")
    op = m.group("op")
    if value_raw is not None:
        val = value_raw.strip()
        if op in ("in", "not in"):
            value = [_parse_scalar(p.strip()) for p in val.split(",") if p.strip()]
        else:
            value = _parse_scalar(val)
    else:
        value = None

    return Atom(
        neg=m.group("neg") is not None,
        key=m.group("key"),
        op=op,
        expected=value,
    )


def _split_logic(expr: str, op: str) -> list[str]:
    """按逻辑运算符拆分，仅在运算符后跟 $/not $ 时生效。"""
    pat = _SPLIT_RE[op]
    parts: list[str] = []
    while True:
        m = pat.search(expr)
        if not m:
            parts.append(expr)
            break
        parts.append(expr[: m.start()])
        expr = expr[m.end():]
    return parts


def parse_condition(expr: str) -> list[list[Atom]]:
    """复合表达式 → ``[[and 组1], [and 组2], ...]``；or 最低，and 居中。

    ``A and B or C`` → ``[[A, B], [C]]``（and 优先于 or）。
    不支持括号嵌套。
    """
    or_groups: list[list[Atom]] = []
    for or_part in _split_logic(expr, "or"):
        and_atoms = [_parse_atom(a) for a in _split_logic(or_part, "and")]
        or_groups.append(and_atoms)
    return or_groups


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


def _eval_atom(ctx: dict[str, Any], atom: Atom) -> bool:
    """单条原子条件在视图上求值。"""
    actual = ctx
    for part in atom.key.split("."):
        if not isinstance(actual, Mapping) or part not in actual:
            actual = None
            break
        actual = actual[part]
    result = _compare(actual, atom.op, atom.expected) if atom.op else bool(actual)
    return not result if atom.neg else result


@lru_cache(maxsize=256)
def _compile(expr: str) -> ConditionFunc:
    """表达式字符串 → ``(视图) -> bool`` 谓词（缓存解析结果，循环内重复求值零开销）。"""
    groups = parse_condition(expr)

    def cond(ctx: dict[str, Any]) -> bool:
        return any(all(_eval_atom(ctx, atom) for atom in and_group) for and_group in groups)

    return cond


# 对外 API 保持不变
compile_condition = _compile


def eval_condition(cond: str | bool, view: dict[str, Any]) -> bool:
    """节点条件求值（原始声明 → bool）：布尔常量 / 表达式字符串（接线视图上）。"""
    if isinstance(cond, bool):
        return cond
    return bool(_compile(cond)(view))
