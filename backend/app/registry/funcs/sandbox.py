"""沙箱工具 — 通过 sandbox 服务执行代码和安装依赖。"""

from pydantic import BaseModel, Field

from app.core.config import settings
from app.registry.types import func
from app.utils.http import http_client


class PipInstallOutput(BaseModel):
    result: str = Field(description="安装结果")


class RunCodeOutput(BaseModel):
    result: str = Field(description="执行输出")


class PipInstallParams(BaseModel):
    packages: str = Field(description="要安装的包名，空格分隔，如 'scipy scikit-learn'", min_length=1)


class RunCodeParams(BaseModel):
    code: str = Field(description="要执行的 Python 代码", min_length=1)
    timeout: int = Field(default=60, description="超时秒数")


@func(
    node=False,
    label="依赖安装",
    description="在沙箱中安装 Python 包。run_code 报 ModuleNotFoundError 时用此工具安装缺失包",
    metadata={"group": "工具"},
)
async def pip_install(params: PipInstallParams) -> PipInstallOutput:
    async with http_client() as client:
        resp = await client.post(
            f"{settings.SANDBOX_URL}/pip",
            json={"packages": params.packages},
            timeout=120,
        )
        result = resp.json()
        if result.get("returncode", -1) != 0:
            raise RuntimeError(f"pip install 失败: {result.get('stderr', '')}")
    return PipInstallOutput(result=result["stdout"].strip())


@func(
    node=False,
    label="代码执行",
    description="在沙箱中执行 Python 代码并返回 stdout。如需保存文件，写入 /uploads 目录。",
    metadata={"group": "工具"},
)
async def run_code(params: RunCodeParams) -> RunCodeOutput:
    async with http_client() as client:
        resp = await client.post(
            f"{settings.SANDBOX_URL}/run",
            json={"code": params.code, "timeout": params.timeout},
            timeout=params.timeout + 5,
        )
        result = resp.json()
        if result.get("returncode", -1) != 0:
            raise RuntimeError(result.get("stderr", "执行失败"))
    return RunCodeOutput(result=result["stdout"].strip())
