"""测试 helper — 从 schema.validate_config 搬入，非生产代码。"""

from typing import Any

from pydantic import ValidationError

from app.engine.pipeline import Pipeline


def validate_config(config: dict[str, Any]) -> list[str]:
    """校验完整 DAG 配置，返回全部错误（空列表 = 合法）。"""
    try:
        Pipeline(**config)
        return []
    except ValidationError as exc:
        errors: list[str] = []
        for err in exc.errors():
            msg = err["msg"].removeprefix("Value error, ")
            loc = err["loc"]
            if len(loc) >= 2 and loc[0] == "nodes" and not msg.startswith("DAG"):
                node_name = loc[1]
                if err["type"] == "missing" and "type" in loc:
                    msg = f"节点 {node_name!r}: 需要 'type'（函数键）"
                else:
                    msg = f"节点 {node_name!r}: {msg}"
            errors.append(msg)
        return errors
    except (ValueError, TypeError) as exc:
        return [str(exc)]
