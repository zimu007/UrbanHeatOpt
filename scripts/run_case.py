"""Public UrbanHeatOpt test-track entry; delegates all work to the new pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from competition.pipelines import run_case_pipeline, run_wuhan_v02_pipeline
from competition.adapters.wuhan_v02_case import WuhanV02CaseError
from competition.readiness import (
    default_audit_output_dir,
    default_guidance_report_path,
    run_guanggu_v03_input_validation,
)
from competition.validation.v3_inputs import V3InputError


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the unified competition Pyomo pipeline.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--case", help="competition_input_3.0.0 draft case directory")
    source.add_argument("--delivery-root", help="Guanggu external delivery directory")
    parser.add_argument("--source-profile", choices=("wuhan_v02", "guanggu_v03"))
    parser.add_argument("--assumption-profile", choices=("provisional_v0",))
    parser.add_argument("--profile", required=True, choices=("v0-smoke", "v1-full"))
    parser.add_argument("--output-root", default="runs")
    parser.add_argument("--guidance-report", type=Path)
    args = parser.parse_args()
    try:
        if args.delivery_root:
            if args.source_profile is None:
                parser.error("--delivery-root 必须同时提供 --source-profile")
            if args.source_profile == "guanggu_v03":
                if args.assumption_profile is not None:
                    parser.error("guanggu_v03 不接受旧 provisional_v0 assumption profile")
                repository = Path(__file__).resolve().parents[1]
                audit_output = default_audit_output_dir(args.output_root)
                guidance = args.guidance_report or default_guidance_report_path()
                try:
                    gate = run_guanggu_v03_input_validation(
                        args.delivery_root,
                        audit_output,
                        guidance,
                        full_audit=True,
                        workspace_root=repository,
                    )
                except (OSError, UnicodeError, ValueError) as exc:
                    print(f"[输入失败] {exc}", file=sys.stderr)
                    return 2
                if not gate.readiness.model_ready:
                    print(
                        "[停止] v0.3源数据及2160小时标准化已通过，但模型尚未就绪；"
                        "未创建求解器、未调用旧模型。",
                        file=sys.stderr,
                    )
                    for item in gate.readiness.blockers:
                        print(f"- {item.item_id}: {item.title} — {item.reason}", file=sys.stderr)
                    print(f"详细报告：{gate.guidance_report_path}", file=sys.stderr)
                    return 2
                print("[失败] 模型就绪但v0.3求解Pipeline尚未登记。", file=sys.stderr)
                return 1
            if args.assumption_profile is None:
                parser.error("wuhan_v02 必须同时提供 --assumption-profile provisional_v0")
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
