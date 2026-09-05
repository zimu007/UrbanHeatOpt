"""Make subprocess regression checks use the same explicit source checkout."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["PYTHONPATH"] = os.pathsep.join(
    [str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")]
)
