"""
沙箱工具 — 通过 compose 中的 sandbox 服务执行代码和安装依赖。
"""

from app.core.config import settings
from app.registry.tool import tool
from app.utils.http import http_client


# ---------------------------------------------------------------------------
# 工具 — 供 agent 调用
# ---------------------------------------------------------------------------


@tool(description="在沙箱中安装 Python 包。run_code 报 ModuleNotFoundError 时用此工具安装缺失包",
      label="依赖安装",
      params={"packages": "要安装的包名，空格分隔，如 'scipy scikit-learn'"})
async def pip_install(packages: str) -> str:
    """在沙箱中 pip install，安装到持久化卷，后续 run_code 可用。"""
    if not packages.strip():
        return "未指定要安装的包"

    resp = await http_client().post(
        f"{settings.SANDBOX_URL}/pip",
        json={"packages": packages},
        timeout=120,
    )
    result = resp.json()
    if resp.status_code != 200:
        raise RuntimeError(result.get("error", result.get("stderr", "pip install 失败")))
    return f"{result['stdout'].strip()}，已安装：{packages}"


@tool(description="执行 Python 代码并返回输出。如需保存文件，写入 /uploads 目录。",
      label="代码执行",
      params={"code": "要执行的 Python 代码"})
async def run_code(code: str, timeout: int = 60) -> str:
    """在 Docker 沙箱中执行 Python 代码，返回 stdout 和 stderr。"""
    resp = await http_client().post(
        f"{settings.SANDBOX_URL}/run",
        json={"code": code, "timeout": timeout},
        timeout=timeout + 5,
    )
    result = resp.json()
    if resp.status_code != 200:
        raise RuntimeError(result.get("error", result.get("stderr", "执行失败")))

    if result["returncode"] != 0:
        raise RuntimeError(result["stderr"])

    return result["stdout"].strip()
