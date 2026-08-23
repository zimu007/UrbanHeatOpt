"""Public UrbanHeatOpt test-track entry; delegates all work to the new pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from competition.pipelines import run_case_pipeline, run_wuhan_v02_pipeline
from competition.adapters.wuhan_v02_case import WuhanV02CaseError
from competition.validation.v3_inputs import V3InputError


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the unified competition Pyomo pipeline.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--case", help="competition_input_3.0.0 draft case directory")
    source.add_argument("--delivery-root", help="Guanggu v0.2 delivery directory")
    parser.add_argument("--source-profile", choices=("wuhan_v02",))
    parser.add_argument("--assumption-profile", choices=("provisional_v0",))
    parser.add_argument("--profile", required=True, choices=("v0-smoke", "v1-full"))
    parser.add_argument("--output-root", default="runs")
    args = parser.parse_args()
    try:
        if args.delivery_root:
            if args.source_profile is None or args.assumption_profile is None:
                parser.error("--delivery-root 必须同时提供 --source-profile 和 --assumption-profile")
            result = run_wuhan_v02_pipeline(
                args.delivery_root,
                source_profile=args.source_profile,
                assumption_profile=args.assumption_profile,
                profile=args.profile,
                output_root=args.output_root,
            )
        else:
            if args.source_profile is not None or args.assumption_profile is not None:
                parser.error("--source-profile/--assumption-profile 只用于 --delivery-root")
            result = run_case_pipeline(
                args.case,
                profile=args.profile,
                output_root=args.output_root,
            )
    except (V3InputError, WuhanV02CaseError) as exc:
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
