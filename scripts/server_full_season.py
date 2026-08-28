"""Prepare and operate the unchanged-core 62-building full-season job set."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from competition.adapters.guanggu_v03_case import prepare_guanggu_v03_v0_case
from competition.full_season_server import (
    assemble_full_season_results,
    create_task_plan,
    extend_unfinished_time_limits,
    finalize_representative_results,
    resolve_epsilon_tasks,
    resolve_representative_tasks,
    run_ready_tasks,
    select_formal_thread_count,
    summarize_benchmarks,
    task_status,
    verify_source_integrity,
)
from competition.osm_corridor import download_osm_snapshot
from competition.readiness import run_guanggu_v03_input_validation


def _prepare(args: argparse.Namespace) -> int:
    root = args.run_root.resolve()
    if root.exists():
        raise FileExistsError(f"RUN目录已存在，拒绝覆盖：{root}")
    root.mkdir(parents=True)
    validation = run_guanggu_v03_input_validation(
        args.delivery_root,
        root / "validation",
        root / "input_guidance_CN.md",
        full_audit=True,
        workspace_root=Path(__file__).resolve().parents[1],
    )
    spatial_root = root / "spatial"
    if args.osm_snapshot is None:
        snapshot = download_osm_snapshot(
            validation.adaptation.canonical_data.buildings,
            spatial_root,
            endpoint=args.overpass_endpoint,
            buffer_m=args.osm_buffer_m,
        )
        snapshot_path = snapshot.response_path
    else:
        source_snapshot = args.osm_snapshot.resolve()
        if not source_snapshot.is_file():
            raise FileNotFoundError(f"指定的OSM快照不存在：{source_snapshot}")
        spatial_root.mkdir()
        snapshot_path = spatial_root / "osm_overpass_snapshot.json"
        shutil.copy2(source_snapshot, snapshot_path)
        (spatial_root / "osm_overpass_snapshot_metadata.json").write_text(
            json.dumps(
                {
                    "source": str(source_snapshot),
                    "response_sha256": sha256(snapshot_path.read_bytes()).hexdigest(),
                    "classification": "user_supplied_frozen_osm_snapshot",
                    "data_attribution": "© OpenStreetMap contributors",
                    "data_license": "ODbL 1.0",
                    "copyright_url": "https://www.openstreetmap.org/copyright",
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    prepared = prepare_guanggu_v03_v0_case(
        validation.adaptation,
        root / "prepared",
        assumption_profile="provisional_v0",
        profile="v0-full-season",
        spatial_profile="osm_main_road_provisional",
        osm_snapshot_path=snapshot_path,
    )
    config_path = prepared.case_dir / "case_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["solver"].update(
        {"threads": 8, "time_limit_seconds": 21600, "mip_gap": 0.01}
    )
    config["pareto"].update({"point_count": 11, "run_epsilon_scan": True})
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    plan = create_task_plan(
        root,
        prepared.case_dir,
        point_count=11,
        physical_core_count=args.physical_cores,
        source_input_sha256=dict(validation.adaptation.source_report.file_sha256),
        source_validation_report_sha256=sha256(
            validation.adaptation.source_report_path.read_bytes()
        ).hexdigest(),
    )
    print(plan)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prepare = sub.add_parser("prepare", help="只读校验并建立62栋×2160h任务目录")
    prepare.add_argument("--delivery-root", required=True, type=Path)
    prepare.add_argument("--run-root", required=True, type=Path)
    prepare.add_argument(
        "--physical-cores",
        required=True,
        type=int,
        help="服务器资产清单中的物理核心数；60核服务器应传60",
    )
    prepare.add_argument(
        "--osm-snapshot",
        type=Path,
        help="可复用的冻结Overpass JSON；省略时只发送园区bbox下载一次",
    )
    prepare.add_argument(
        "--overpass-endpoint",
        default="https://overpass-api.de/api/interpreter",
    )
    prepare.add_argument("--osm-buffer-m", type=float, default=800.0)
    status = sub.add_parser("status", help="查看任务状态")
    status.add_argument("--run-root", required=True, type=Path)
    resolve = sub.add_parser("resolve-epsilon", help="六端点完成后生成33个epsilon值")
    resolve.add_argument("--run-root", required=True, type=Path)
    representatives = sub.add_parser(
        "resolve-representatives", help="39个主任务完成后生成千分之一gap代表解认证任务"
    )
    representatives.add_argument("--run-root", required=True, type=Path)
    representatives.add_argument(
        "--policy-carbon-constraint-kgco2e-per-year",
        required=True,
        type=float,
        help="经老师或项目确认的年度运行碳排上限",
    )
    run = sub.add_parser("run", help="运行一个阶段的所有待办任务")
    run.add_argument("--run-root", required=True, type=Path)
    run.add_argument(
        "--phase",
        required=True,
        choices=("benchmark", "endpoint", "epsilon", "representative"),
    )
    run.add_argument("--max-workers", type=int)
    run.add_argument(
        "--physical-cores",
        type=int,
        help="物理核心数；省略时采用任务计划中prepare阶段记录值",
    )
    run.add_argument("--memory-per-task-gib", type=float, default=12.0)
    benchmark_summary = sub.add_parser(
        "summarize-benchmarks", help="核对1/4/8线程MPS并汇总求解进展"
    )
    benchmark_summary.add_argument("--run-root", required=True, type=Path)
    select_threads = sub.add_parser(
        "select-threads", help="根据基准证据冻结正式任务的单任务线程数"
    )
    select_threads.add_argument("--run-root", required=True, type=Path)
    select_threads.add_argument("--threads", required=True, type=int, choices=(1, 4, 8))
    extend = sub.add_parser(
        "extend-time", help="将未完成端点或epsilon任务从6小时延长到12小时"
    )
    extend.add_argument("--run-root", required=True, type=Path)
    extend.add_argument("--phase", required=True, choices=("endpoint", "epsilon"))
    extend.add_argument("--hours", type=int, default=12, choices=(12,))
    assemble = sub.add_parser("assemble", help="汇总39个完整全季任务")
    assemble.add_argument("--run-root", required=True, type=Path)
    finalize = sub.add_parser(
        "finalize-representatives", help="核验并汇总千分之一gap代表解认证结果"
    )
    finalize.add_argument("--run-root", required=True, type=Path)
    verify_source = sub.add_parser(
        "verify-source-integrity", help="求解后重新校验v0.2并核对全部源文件哈希"
    )
    verify_source.add_argument("--run-root", required=True, type=Path)
    verify_source.add_argument("--delivery-root", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        return _prepare(args)
    if args.action == "status":
        print(json.dumps(task_status(args.run_root), ensure_ascii=False, indent=2))
        return 0
    if args.action == "resolve-epsilon":
        print(resolve_epsilon_tasks(args.run_root))
        return 0
    if args.action == "resolve-representatives":
        print(
            resolve_representative_tasks(
                args.run_root,
                policy_carbon_constraint_kgCO2e_per_year=(
                    args.policy_carbon_constraint_kgco2e_per_year
                ),
            )
        )
        return 0
    if args.action == "summarize-benchmarks":
        print(summarize_benchmarks(args.run_root))
        return 0
    if args.action == "select-threads":
        print(select_formal_thread_count(args.run_root, threads=args.threads))
        return 0
    if args.action == "extend-time":
        print(
            extend_unfinished_time_limits(
                args.run_root,
                phase=args.phase,
                time_limit_hours=args.hours,
            )
        )
        return 0
    if args.action == "run":
        physical_cores = args.physical_cores
        if physical_cores is None:
            plan = json.loads(
                (args.run_root.resolve() / "task_plan.json").read_text(encoding="utf-8")
            )
            physical_cores = plan.get("host_environment", {}).get(
                "physical_core_count"
            )
        if physical_cores is None and args.max_workers is None:
            raise ValueError(
                "任务计划未记录物理核心数；请传--physical-cores或显式--max-workers"
            )
        print(
            json.dumps(
                run_ready_tasks(
                    args.run_root,
                    phase=args.phase,
                    maximum_workers=args.max_workers,
                    memory_per_task_gib=args.memory_per_task_gib,
                    physical_core_count=physical_cores,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.action == "verify-source-integrity":
        root = args.run_root.resolve()
        final_validation = run_guanggu_v03_input_validation(
            args.delivery_root,
            root / "final_source_integrity_validation",
            root / "final_source_integrity_guidance_CN.md",
            full_audit=True,
            workspace_root=Path(__file__).resolve().parents[1],
        )
        print(
            verify_source_integrity(
                root,
                current_source_input_sha256=dict(
                    final_validation.adaptation.source_report.file_sha256
                ),
                current_source_validation_report=(
                    final_validation.adaptation.source_report_path
                ),
            )
        )
        return 0
    if args.action == "assemble":
        print(assemble_full_season_results(args.run_root))
        return 0
    print(finalize_representative_results(args.run_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
