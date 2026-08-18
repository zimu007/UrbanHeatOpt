from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from competition.validation.inputs import InputValidationError, validate_case_inputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a competition case input directory.")
    parser.add_argument("--case", required=True, help="Path to a case directory containing case_config.yaml.")
    args = parser.parse_args()

    try:
        inputs = validate_case_inputs(Path(args.case))
    except InputValidationError as exc:
        print("[失败] 输入校验未通过：", file=sys.stderr)
        for error in exc.errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("[通过] 输入校验通过")
    print(f"case_id={inputs.config['case_id']}")
    print(f"scenario_id={inputs.config['scenario_id']}")
    print(f"buildings={len(inputs.buildings)}")
    print(f"hours={len(inputs.timestamp_hour_map)}")
    print(f"technologies={len(inputs.technologies)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
