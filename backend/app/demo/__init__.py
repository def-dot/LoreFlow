"""
Demo pipeline shared by the web app (app.main) and examples.py.
"""

from pathlib import Path

from .functions import FUNCTIONS

#: Path to the declarative pipeline definition shipped with the package.
PIPELINE_PATH = Path(__file__).parent / "pipeline.yaml"

__all__ = ["FUNCTIONS", "PIPELINE_PATH"]
