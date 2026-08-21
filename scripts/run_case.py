"""Public UrbanHeatOpt test-track entry; delegates all work to the new pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from competition.pipelines import run_case_pipeline
from competition.validation.v3_inputs import V3InputError


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the unified competition Pyomo pipeline.")
    parser.add_argument("--case", required=True, help="competition_input_3.0.0 draft case directory")
    parser.add_argument("--profile", required=True, choices=("v0-smoke", "v1-full"))
    parser.add_argument("--output-root", default="runs")
    args = parser.parse_args()
    try:
        result = run_case_pipeline(args.case, profile=args.profile, output_root=args.output_root)
    except V3InputError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[失败] 统一竞赛主线异常：{exc}", file=sys.stderr)
        return 1
    print("[通过] 统一竞赛主线完成")
    print(result.manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
