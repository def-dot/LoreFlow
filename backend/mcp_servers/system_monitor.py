"""系统监控 MCP 服务器 — 提供系统状态、进程、日志查询能力。

启动方式（stdio）：
    python backend/mcp_servers/system_monitor.py [--logs-dir PATH]
"""

from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

LOGS_DIR: Path = Path("backend/logs")

server = Server("system-monitor")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="system_info",
            description="获取系统状态：CPU、内存、磁盘使用情况",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="process_list",
            description="列出当前运行的进程（可按关键词过滤）",
            inputSchema={
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "description": "进程名过滤关键词（如 python、uvicorn、node）",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="read_logs",
            description="读取项目日志文件的最近 N 行",
            inputSchema={
                "type": "object",
                "properties": {
                    "lines": {
                        "type": "integer",
                        "description": "读取行数，默认 50",
                    },
                    "level": {
                        "type": "string",
                        "description": "按日志级别过滤（DEBUG/INFO/WARNING/ERROR/CRITICAL）",
                    },
                },
                "required": [],
            },
        ),
    ]


def _text(content: str) -> list[TextContent]:
    return [TextContent(type="text", text=content)]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "system_info":
        return _text(_system_info())
    if name == "process_list":
        return _text(_process_list(arguments.get("filter", "")))
    if name == "read_logs":
        return _text(_read_logs(
            arguments.get("lines", 50),
            arguments.get("level"),
        ))
    return _text(f"未知工具：{name}")


# ---------------------------------------------------------------------------
# 实现
# ---------------------------------------------------------------------------

def _system_info() -> str:
    """CPU / 内存 / 磁盘使用情况（纯标准库，不依赖 psutil）。"""
    parts: list[str] = []

    # — CPU —
    try:
        load = os.getloadavg()  # type: ignore[attr-defined]
        parts.append(f"CPU 负载（1/5/15min）：{load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}")
    except (OSError, AttributeError):
        # Windows 没有 getloadavg
        parts.append("CPU 负载：不可用（Windows）")

    # — 内存 —
    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                key, val = line.split(":")[:2]
                mem[key.strip()] = int(val.strip().split()[0])
            total = mem.get("MemTotal", 0)
            avail = mem.get("MemAvailable", 0)
            used_pct = (1 - avail / total) * 100 if total else 0
            parts.append(f"内存：{total // 1024} MB 总计，{avail // 1024} MB 可用（{used_pct:.1f}% 已用）")
    except FileNotFoundError:
        parts.append("内存：不可用（非 Linux）")

    # — 磁盘 —
    usage = shutil.disk_usage("/")
    parts.append(f"磁盘 (/)：{usage.total // (1024**3)} GB 总计，{usage.free // (1024**3)} GB 可用")

    return "\n".join(parts)


def _process_list(filter_kw: str) -> str:
    """列出进程（纯标准库）。"""
    import subprocess

    try:
        result = subprocess.run(
            ["ps", "aux", "--no-headers"],
            capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.strip().splitlines()
    except FileNotFoundError:
        # Windows fallback
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.strip().splitlines()

    if filter_kw:
        kw = filter_kw.lower()
        lines = [l for l in lines if kw in l.lower()]

    if not lines:
        return "无匹配进程"

    # 最多返回 30 行
    header = f"共 {len(lines)} 个进程" + (f"（过滤：{filter_kw}）" if filter_kw else "")
    return header + "\n" + "\n".join(lines[:30])


def _read_logs(lines: int, level: str | None) -> str:
    """读取最近日志。"""
    log_files = sorted(LOGS_DIR.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not log_files:
        return f"日志目录为空：{LOGS_DIR}"

    log_file = log_files[0]
    try:
        all_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return f"读取日志失败：{e}"

    if level:
        level_upper = level.upper()
        all_lines = [l for l in all_lines if level_upper in l]

    tail = all_lines[-lines:]
    if not tail:
        return "无匹配日志"

    return f"文件：{log_file.name}（最后 {len(tail)} 行）\n" + "\n".join(tail)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    parser = argparse.ArgumentParser()
    parser.add_argument("--logs-dir", default="backend/logs")
    args = parser.parse_args()
    LOGS_DIR = Path(args.logs_dir)

    async def _main():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_main())
