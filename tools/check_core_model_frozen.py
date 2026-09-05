"""Verify that the server run uses the frozen competition core model."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "src/urbanheatopt/data/profile_resources/core_model_freeze.yaml"


def main() -> int:
    record = yaml.safe_load(FREEZE.read_text(encoding="utf-8"))
    target = ROOT / str(record["core_model_path"])
    actual = sha256(target.read_bytes()).hexdigest()
    expected = str(record["sha256"]).lower()
    print(f"核心模型：{target}")
    print(f"冻结 SHA-256：{expected}")
    print(f"当前 SHA-256：{actual}")
    if actual != expected:
        print("[失败] 核心模型与冻结记录不一致，禁止服务器正式求解。", file=sys.stderr)
        return 2
    print("[通过] 核心模型与冻结记录一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
