"""A-owned entry shell. Model and reporting capabilities are explicit gates."""
import argparse
import json
from pathlib import Path
import sys

from urbanheatopt.paths import REPOSITORY_ROOT


def main(argv=None):
    parser = argparse.ArgumentParser(description="UrbanHeatOpt V2 输入与集成入口；V1仅显式回放")
    parser.add_argument("command", choices=("validate", "prepare", "solve", "report", "diagnose", "tes-check"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--study-root", type=Path)
    parser.add_argument("--tes-sensitivity-root", type=Path)
    parser.add_argument("--tes-capex-multipliers", default="1,0.5,0.25,0.1")
    parser.add_argument("--threads", type=int, choices=(1, 4, 8), default=4)
    request_group = parser.add_mutually_exclusive_group()
    request_group.add_argument("--request", type=Path)
    request_group.add_argument(
        "--request-set",
        choices=("cost-endpoints", "carbon-endpoints", "pareto-knee", "full-study"),
    )
    args = parser.parse_args(argv)
    try:
        if args.command in {"validate", "prepare"}:
            if args.config is None:
                raise ValueError(f"{args.command}必须提供--config")
            from urbanheatopt.data.integration import run_input_pipeline
            result = run_input_pipeline(args.command, args.config, run_id=args.run_id, output_root=args.output_root)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return result["exit_code"]
        if args.command == "report":
            if args.run_root is None or args.output_root is None:
                raise ValueError("report必须同时提供--run-root和--output-root")
            from urbanheatopt.reporting.full_study import generate_full_study_report

            report = generate_full_study_report(
                args.run_root,
                args.output_root,
                tes_sensitivity_root=args.tes_sensitivity_root,
            )
            print(json.dumps({
                "status": "completed",
                "output_directory": str(report.output_directory),
                "figure_manifest": str(report.manifest),
                "atlas_pdf": str(report.atlas_pdf),
            }, ensure_ascii=False, indent=2))
            return 0
        if args.command == "tes-check":
            if any(value is None for value in (
                args.bundle, args.study_root, args.run_id, args.output_root,
            )):
                raise ValueError(
                    "tes-check必须同时提供--bundle、--study-root、--run-id和--output-root"
                )
            from urbanheatopt.optimization.solve_executor import (
                execute_tes_capex_sensitivity_set,
                validate_run_id,
            )

            multipliers = tuple(
                float(value.strip())
                for value in args.tes_capex_multipliers.split(",")
                if value.strip()
            )
            output_root = args.output_root.expanduser()
            if not output_root.is_absolute():
                output_root = REPOSITORY_ROOT / output_root
            output_root = output_root.resolve()
            allowed_root = (REPOSITORY_ROOT / "runs").resolve()
            if not output_root.is_relative_to(allowed_root):
                raise ValueError("tes-check输出必须位于仓库runs目录")
            target = output_root / validate_run_id(args.run_id)
            payload = execute_tes_capex_sensitivity_set(
                args.bundle,
                args.study_root,
                target,
                multipliers=multipliers,
                threads=args.threads,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0 if payload["demonstration_achieved"] else 2
        if args.config is None:
            raise ValueError(f"{args.command}必须提供--config")
        from urbanheatopt.data.integration import load_config
        load_config(args.config)
        if args.command == "solve":
            if args.bundle is None or args.run_id is None or args.output_root is None:
                raise ValueError("solve必须同时提供--bundle、--run-id和--output-root")
            if args.request is None and args.request_set is None:
                raise ValueError("solve必须提供--request或--request-set")
            from urbanheatopt.optimization.solve_executor import (
                execute_prepared_request,
                execute_request_set,
                validate_run_id,
            )
            run_id = validate_run_id(args.run_id)
            output_root = args.output_root.expanduser()
            if not output_root.is_absolute():
                output_root = REPOSITORY_ROOT / output_root
            output_root = output_root.resolve()
            allowed_root = (REPOSITORY_ROOT / "runs").resolve()
            if not output_root.is_relative_to(allowed_root):
                raise ValueError("solve输出必须位于仓库runs目录")
            target = output_root / run_id
            if args.request is not None:
                result = execute_prepared_request(args.bundle, args.request, target)
                payload = {
                    "run_id": run_id,
                    "result_bundle_id": result.bundle_id,
                    "result_bundle": str((target / "result_bundle.json").resolve()),
                    "qualified": result.to_dict()["qualified"],
                }
            else:
                payload = execute_request_set(args.bundle, target, args.request_set)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if args.bundle:
            from urbanheatopt.data.bundles import CaseBundle, ready_report
            evidence_path = args.bundle.resolve().parent / "ab_adapter_smoke.json"
            evidence = (
                json.loads(evidence_path.read_text(encoding="utf-8"))
                if evidence_path.is_file() else None
            )
            result = ready_report(
                CaseBundle.read(args.bundle), integration_evidence=evidence
            )
        else:
            result = {"model_ready": False, "solver_executed": False,
                      "reason": "B/C消费适配器尚未交接；请先validate/prepare。没有旧模型回退。"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    except (ValueError, OSError) as exc:
        print(f"[输入/接口错误] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[程序异常] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
