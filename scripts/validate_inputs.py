from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from competition.intake import validate_wuhan_v02_delivery
from competition.validation.inputs import InputValidationError, validate_case_inputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a competition case input directory.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--case", type=Path, help="Path to a legacy competition case directory.")
    source.add_argument("--delivery-root", type=Path, help="Path to a source delivery directory.")
    parser.add_argument("--source-profile", choices=("wuhan_v02",))
    parser.add_argument("--full-audit", action="store_true")
    args = parser.parse_args()

    if args.delivery_root is not None:
        if args.source_profile != "wuhan_v02":
            parser.error("--delivery-root 当前必须配合 --source-profile wuhan_v02")
        try:
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
