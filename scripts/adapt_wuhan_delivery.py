from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from competition.adapters.wuhan_delivery import adapt_wuhan_delivery


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a validation-only standard case from the current Wuhan delivery.")
    parser.add_argument("--source", type=Path, default=Path.cwd(), help="Wuhan delivery root (default: current directory).")
    parser.add_argument("--output", type=Path, default=Path("runs/wuhan_delivery_validation"), help="Generated standard case directory (default: runs/wuhan_delivery_validation).")
    parser.add_argument("--year", type=int, default=2025, help="Non-leap calendar year used for the 8760 timestamps (default: 2025).")
    args = parser.parse_args()
    try:
        result = adapt_wuhan_delivery(args.source, args.output, year=args.year)
    except Exception as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    report = result.validation_report
    print(json.dumps({"status": "valid", "output_dir": str(result.output_dir), "building_count": len(report.buildings), "hour_count": len(report.timestamp_hour_map), "technology_count": len(report.technologies), "max_reconciliation_error": float(result.reconciliation["relative_error"].abs().max())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
