from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from competition.adapters.legacy_case import adapt_case_to_legacy
from competition.validation.inputs import InputValidationError


def main() -> int:
    parser = argparse.ArgumentParser(description="Adapt a validated competition case to legacy UrbanHeatOpt files.")
    parser.add_argument("--case", required=True, help="Path to a competition case directory.")
    parser.add_argument("--output", required=True, help="Output directory for legacy files.")
    args = parser.parse_args()

    try:
        result = adapt_case_to_legacy(Path(args.case), Path(args.output))
    except InputValidationError as exc:
        print("[失败] 输入校验未通过，未生成 legacy 文件：", file=sys.stderr)
        for error in exc.errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("[通过] 已生成 legacy UrbanHeatOpt 文件")
    print(result.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
