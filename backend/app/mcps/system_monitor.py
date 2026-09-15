"""系统监控 MCP 服务器 — 提供系统状态查询能力。

启动方式（stdio）：
    uv run python backend/mcp_servers/system_monitor.py
"""

from __future__ import annotations

import json

import psutil
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("system-monitor")


@mcp.tool()
def system_info() -> str:
    """获取系统状态：CPU、内存、磁盘使用情况。"""
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return json.dumps({
        "cpu": {
            "count": psutil.cpu_count(),
            "percent": psutil.cpu_percent(interval=1),
        },
        "memory": {
            "total_mb": mem.total // (1024**2),
            "available_mb": mem.available // (1024**2),
            "percent": mem.percent,
        },
        "disk": {
            "total_gb": round(disk.total / (1024**3), 1),
            "free_gb": round(disk.free / (1024**3), 1),
            "percent": disk.percent,
        },
    }, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
