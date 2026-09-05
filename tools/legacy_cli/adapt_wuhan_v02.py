from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from urbanheatopt.data.adapters import adapt_wuhan_v02_sources


def main() -> int:
    parser = argparse.ArgumentParser(description="Adapt Wuhan Guanggu v0.2 source files.")
    parser.add_argument("--delivery-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--source-profile", required=True, choices=("wuhan_v02",))
    parser.add_argument("--skip-full-audit", action="store_true")
    args = parser.parse_args()
    try:
        result = adapt_wuhan_v02_sources(
            args.delivery_root,
            args.output_dir,
            full_audit=not args.skip_full_audit,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"[失败] v0.2 适配失败：{exc}", file=sys.stderr)
        return 2
    print(f"[通过] v0.2 标准化完成：{result.output_dir}")
    print(f"buildings={result.building_count}, hours={result.hour_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
