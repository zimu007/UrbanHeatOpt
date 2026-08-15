from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from competition.validation.inputs import InputValidationError, validate_case_inputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a competition case input directory.")
    parser.add_argument(
        "--case",
        required=True,
        type=Path,
        help="Path to a case directory containing case_config.yaml.",
    )
    args = parser.parse_args()

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
