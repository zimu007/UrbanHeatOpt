from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from competition.intake import IntakeValidationError, validate_delivery


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an external data delivery from a manifest.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-root", type=Path, help="Override the manifest-relative source root.")
    args = parser.parse_args()
    try:
        report = validate_delivery(args.manifest, args.source_root)
    except (IntakeValidationError, OSError, UnicodeError, ValueError) as exc:
        print(json.dumps({"status": "invalid", "errors": [{"code": "MANIFEST_INVALID", "message": str(exc)}]}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    stream = sys.stdout if report.valid else sys.stderr
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), file=stream)
    return 0 if report.valid else 2


if __name__ == "__main__":
    raise SystemExit(main())

