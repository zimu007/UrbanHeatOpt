from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from urbanheatopt.reporting.results.standard import export_standard_results


def main() -> int:
    parser = argparse.ArgumentParser(description="Export standard P0 result tables from legacy UrbanHeatOpt outputs.")
    parser.add_argument("--case", required=True, help="Competition case input directory.")
    parser.add_argument("--legacy-case-dir", required=True, help="Generated legacy case directory.")
    args = parser.parse_args()

    result = export_standard_results(args.case, args.legacy_case_dir)
    print("[通过] 标准结果已导出")
    print(result.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
