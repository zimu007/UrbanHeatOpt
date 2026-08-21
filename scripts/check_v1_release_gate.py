"""Check whether an existing run is eligible to be named UrbanHeatOpt V1.0."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from competition.release_gate import assess_v1_release


def main() -> int:
    parser = argparse.ArgumentParser(description="UrbanHeatOpt V1.0 read-only release gate")
    parser.add_argument("--case", required=True)
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    report = assess_v1_release(args.case, args.run_dir)
    # ASCII-safe JSON avoids conda-run/GBK console failures on Chinese Windows;
    # consumers recover the original Chinese text when parsing JSON.
    print(json.dumps(report.to_dict(), ensure_ascii=True, indent=2))
    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
