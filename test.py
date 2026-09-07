# import inspect

def greet(name: str, age: int = 18) -> str:
    return f"Hello {name}"

# sig = inspect.signature(greet)
# for k, v in sig.parameters.items():
#     print(k, v)


# from __future__ import annotations

def process(item: int) -> str:
    pass

# print(process.__annotations__)

from typing import get_type_hints

hints = get_type_hints(greet)
print(hints)