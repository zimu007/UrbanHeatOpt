from pathlib import Path
import subprocess
import sys

from tools.verify_layout import verify


def test_mathematical_bodies_are_unchanged():
    assert verify()["passed"]


def test_entry_does_not_import_legacy():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "run.py", "--help"], cwd=root, capture_output=True)
    assert result.returncode == 0, result.stderr
