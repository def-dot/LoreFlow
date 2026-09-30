"""跨接口共用的枚举型字段。"""

from pydantic import BaseModel


class SourceInfo(BaseModel):
    """能力来源：内置 / 插件文件 / MCP 服务器。"""

    kind: str = "builtin"  # builtin | plugin | mcp
    name: str = ""  # 插件文件名或 MCP 服务器名；builtin 为空
