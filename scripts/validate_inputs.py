from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from competition.intake import validate_wuhan_v02_delivery
from competition.readiness import (
    default_audit_output_dir,
    default_guidance_report_path,
    run_guanggu_v03_input_validation,
)
from competition.validation.inputs import InputValidationError, validate_case_inputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a competition case input directory.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--case", type=Path, help="Path to a legacy competition case directory.")
    source.add_argument("--delivery-root", type=Path, help="Path to a source delivery directory.")
    parser.add_argument("--source-profile", choices=("wuhan_v02", "guanggu_v03"))
    parser.add_argument("--full-audit", action="store_true")
    parser.add_argument(
        "--scope",
        choices=("heating-season",),
        help="guanggu_v03 当前只接受完整供暖季标准化边界。",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        help="guanggu_v03 标准化输出根目录；默认写入仓库 runs/input_validation。",
    )
    parser.add_argument(
        "--guidance-report",
        type=Path,
        help="自动说明MD路径；guanggu_v03 默认写入当前用户桌面。",
    )
    args = parser.parse_args()

    if args.delivery_root is not None:
        if args.source_profile is None:
            parser.error("--delivery-root 必须同时提供 --source-profile")
        if args.source_profile == "guanggu_v03" and args.scope not in (None, "heating-season"):
            parser.error("guanggu_v03 只支持 --scope heating-season")
        try:
            if args.source_profile == "guanggu_v03":
                repository = Path(__file__).resolve().parents[1]
                output = args.output_root or default_audit_output_dir(repository / "runs")
                guidance = args.guidance_report or default_guidance_report_path()
                run = run_guanggu_v03_input_validation(
                    args.delivery_root,
                    output,
                    guidance,
                    full_audit=args.full_audit,
                    workspace_root=repository,
                )
                print(json.dumps(run.to_dict(), ensure_ascii=False, indent=2))
                return 0
            else:
                report = validate_wuhan_v02_delivery(
                    args.delivery_root,
                    full_audit=args.full_audit,
                )
        except (OSError, UnicodeError, ValueError) as exc:
            print(
                json.dumps(
                    {"status": "invalid", "errors": [{"code": "SOURCE_PROFILE_ERROR", "message": str(exc)}]},
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stderr,
            )
            return 2
        stream = sys.stdout if report.valid else sys.stderr
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), file=stream)
        return 0 if report.valid else 2

    try:
        inputs = validate_case_inputs(args.case)
    except InputValidationError as exc:
        print(
            json.dumps(
                {"status": "invalid", "errors": exc.issues},
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2

    print(
        json.dumps(
            {
                "status": "valid",
                "case_dir": str(inputs.case_dir),
                "case_id": inputs.config["case_id"],
                "scenario_id": inputs.config["scenario_id"],
                "data_version": inputs.config["data_version"],
                "building_count": len(inputs.buildings),
                "hour_count": len(inputs.timestamp_hour_map),
                "technology_count": len(inputs.technologies),
                "file_sha256": inputs.file_sha256,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
