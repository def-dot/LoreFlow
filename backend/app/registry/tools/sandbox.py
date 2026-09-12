"""
沙箱工具 — Docker 容器内执行代码与安装依赖。
"""

import asyncio
import os
import tempfile

from app.core.config import settings
from app.registry.tool import tool

SANDBOX_IMAGE = "python:3.12-slim"
SANDBOX_PACKAGES_VOLUME = "loreflow-sandbox-packages"
DOCKER_PACKAGES_PATH = "/opt/packages"


@tool(description="在沙箱中安装 Python 包。run_code 报 ModuleNotFoundError 时用此工具安装缺失包",
      params={"packages": "要安装的包名，空格分隔，如 'scipy scikit-learn'"})
async def pip_install(packages: str) -> str:
    """在沙箱中 pip install，安装到持久化卷，后续 run_code 可用。"""
    if not packages.strip():
        return "未指定要安装的包"
    cmd = [
        "docker", "run", "--rm",
        "--memory", "256m", "--cpus", "0.5",
        "-v", f"{SANDBOX_PACKAGES_VOLUME}:{DOCKER_PACKAGES_PATH}",
        SANDBOX_IMAGE,
        "sh", "-c",
        f"pip install --no-cache-dir --target {DOCKER_PACKAGES_PATH} {packages}",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"pip install 失败：\n{stderr.decode()}")
    return f"{stdout.decode().strip()}，已安装：{packages}"


@tool(description="执行 Python 代码并返回输出。如需保存文件，写入 /uploads 目录。",
      params={"code": "要执行的 Python 代码"})
async def run_code(code: str, timeout: int = 60) -> str:
    """在 Docker 沙箱中执行 Python 代码，返回 stdout 和 stderr。"""
    uploads_dir = settings.UPLOADS_DIR.resolve()

    # 写入临时文件（避免命令行转义问题）
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        code_file = f.name

    try:
        cmd = [
            "docker", "run", "--rm",
            "--memory", "256m", "--cpus", "0.5",
            "--read-only",
            "--tmpfs", "/tmp:size=64m",
            "-v", f"{uploads_dir}:/uploads:rw",
            "-v", f"{SANDBOX_PACKAGES_VOLUME}:{DOCKER_PACKAGES_PATH}:ro",
            "-v", f"{code_file}:/tmp/code.py:ro",
            "-e", f"PYTHONPATH={DOCKER_PACKAGES_PATH}",
            SANDBOX_IMAGE,
            "python", "/tmp/code.py",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

        if proc.returncode != 0:
            raise RuntimeError(stderr.decode())

        return stdout.decode().strip()

    finally:
        os.unlink(code_file)
