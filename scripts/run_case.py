"""Public UrbanHeatOpt test-track entry; delegates all work to the new pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from competition.pipelines import (
    run_case_pipeline,
    run_guanggu_v03_pipeline,
    run_wuhan_v02_pipeline,
)
from competition.adapters.wuhan_v02_case import WuhanV02CaseError
from competition.adapters.guanggu_v03_case import GuangguV03CaseError
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
    parser.add_argument(
        "--profile",
        required=True,
        choices=("v0-smoke", "v0-168h", "v0-full-season", "v1-full"),
    )
    parser.add_argument("--output-root", default="runs")
    parser.add_argument("--run-id")
    parser.add_argument("--guidance-report", type=Path)
    parser.add_argument("--core-version", choices=("frozen_v1", "road_joint_v2"), default="frozen_v1")
    parser.add_argument("--osm-snapshot", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--allowed-corridors", type=Path)
    parser.add_argument("--forbidden-areas", type=Path)
    parser.add_argument("--obstacle-buildings", type=Path)
    args = parser.parse_args()
    try:
        if args.core_version == "road_joint_v2":
            if not args.delivery_root or args.source_profile != "guanggu_v03" or not args.osm_snapshot or not args.run_id:
                parser.error("道路V2必须提供delivery-root、guanggu_v03、osm-snapshot和唯一run-id")
            if args.profile!='v1-full' and args.assumption_profile!='provisional_v0':
                parser.error("道路V2测试必须显式指定--assumption-profile provisional_v0")
            from competition.road_joint_v2.pipeline import prepare_delivery
            from competition.road_joint_v2.tasks import run_task
            try:
                root = prepare_delivery(args.delivery_root,args.osm_snapshot,args.output_root,args.run_id,profile=args.profile,
                    allowed_corridors=args.allowed_corridors,forbidden_areas=args.forbidden_areas,obstacle_buildings=args.obstacle_buildings)
                if args.prepare_only:
                    print(f"[输入就绪但未求解] {root}")
                else:
                    run_task(root,"central-cost")
                    print(f"[仅集中式成本端点通过] {root}；其余端点/Pareto未完成")
                return 0
            except (ValueError,OSError) as exc:
                print(f"[V2校验或任务未通过] {exc}；阶段状态以准备报告/任务证据为准",file=sys.stderr)
                return 2
        if args.delivery_root:
            if args.source_profile is None:
                parser.error("--delivery-root 必须同时提供 --source-profile")
            if args.source_profile == "guanggu_v03":
                if args.profile == "v1-full" and args.assumption_profile is not None:
                    parser.error("v1-full 不接受 provisional_v0 假设")
                if args.profile != "v1-full" and args.assumption_profile != "provisional_v0":
                    parser.error("guanggu_v03 的 V0 运行必须提供 --assumption-profile provisional_v0")
                repository = Path(__file__).resolve().parents[1]
                audit_output = default_audit_output_dir(args.output_root)
                guidance = args.guidance_report or audit_output / "input_guidance_CN.md"
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
                if args.profile == "v1-full" and not gate.readiness.model_ready:
                    print(
                        "[停止] v0.3源数据及2160小时标准化已通过，但模型尚未就绪；"
                        "未创建求解器、未调用旧模型。",
                        file=sys.stderr,
                    )
                    for item in gate.readiness.blockers:
                        print(f"- {item.item_id}: {item.title} — {item.reason}", file=sys.stderr)
                    print(f"详细报告：{gate.guidance_report_path}", file=sys.stderr)
                    return 2
                if args.profile == "v1-full":
                    print("[停止] V1正式输入门禁尚未形成可执行正式案例。", file=sys.stderr)
                    return 2
                result = run_guanggu_v03_pipeline(
                    gate,
                    assumption_profile=args.assumption_profile,
                    profile=args.profile,
                    output_root=args.output_root,
                    run_id=args.run_id,
                )
                print("[通过] 光谷v0.3 V0联调主线完成；结果不代表正式工程结论")
                print(result.manifest_path)
                return 0
            if args.assumption_profile is None:
                parser.error("wuhan_v02 必须同时提供 --assumption-profile provisional_v0")
            result = run_wuhan_v02_pipeline(
                args.delivery_root,
                source_profile=args.source_profile,
                assumption_profile=args.assumption_profile,
                profile=args.profile,
                output_root=args.output_root,
                run_id=args.run_id,
            )
        else:
            if args.source_profile is not None or args.assumption_profile is not None:
                parser.error("--source-profile/--assumption-profile 只用于 --delivery-root")
            result = run_case_pipeline(
                args.case,
                profile=args.profile,
                output_root=args.output_root,
                run_id=args.run_id,
            )
    except (V3InputError, WuhanV02CaseError, GuangguV03CaseError) as exc:
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
