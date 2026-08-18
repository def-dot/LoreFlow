"""
Demo pipeline used by the web app (app.main).

节点函数在 app/registry 注册，这里只保留演示用的编排 YAML。
"""

from pathlib import Path

#: Path to the declarative pipeline definition shipped with the package.
PIPELINE_PATH = Path(__file__).parent / "pipeline.yaml"

__all__ = ["PIPELINE_PATH"]
