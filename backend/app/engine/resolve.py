"""
Value conversion for the declarative layer.

Turns config fragments — exception names, retry specs — into engine
objects (exception classes, ``RetryPolicy``). Function-key resolution
lives in :mod:`app.registry` (``REGISTRY``).
"""

from __future__ import annotations

import builtins
import importlib
from functools import cache
from typing import Any, cast

from .pipeline import RetryPolicy


@cache
def import_attr(key: str) -> Any:
    """Resolve a dotted path by importing the longest module prefix and
    walking the remaining attributes. Returns ``None`` if unresolvable."""
    parts = key.split(".")
    for i in range(len(parts), 0, -1):
        try:
            value: Any = importlib.import_module(".".join(parts[:i]))
        except ModuleNotFoundError:
            continue  # this prefix isn't a module — try a shorter one
        try:
            for part in parts[i:]:
                value = getattr(value, part)
        except AttributeError:
            continue  # attribute missing — try a shorter prefix
        return value
    return None


@cache
def resolve_exception(name: str) -> type:
    """Resolve an exception name like ``RuntimeError`` or ``my_errors.MyError``."""
    value = getattr(builtins, name, None)
    if value is None:
        value = import_attr(name)
    if isinstance(value, type) and issubclass(value, BaseException):
        return cast(type, value)
    raise ValueError(f"retry_on 中的未知异常 {name!r}")


def parse_retry(spec: dict[str, Any]) -> RetryPolicy:
    """Parse a retry mapping (dict → RetryPolicy)。"""
    fields = dict(spec)
    names = fields.pop("retry_on", None)
    if names:
        if isinstance(names, str):
            names = [names]
        fields["retry_on"] = tuple(resolve_exception(n) for n in names)
    return RetryPolicy(**fields)
