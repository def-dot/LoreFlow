"""文件工具。"""

from pathlib import Path

from app.registry.tool import tool


@tool(description="读取本地文件内容", params={"path": "文件路径"})
async def read_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return f"文件不存在：{path}"
    if not p.is_file():
        return f"不是文件：{path}"
    try:
        return p.read_text(encoding="utf-8")
    except Exception as exc:
        return f"读取失败：{exc}"
