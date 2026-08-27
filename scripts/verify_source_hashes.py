"""Recompute every accepted v0.2 source hash and compare with a baseline report."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from competition.intake import resolve_guanggu_v03_source_roots


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--baseline-report", required=True, type=Path)
    args = parser.parse_args()
    roots = resolve_guanggu_v03_source_roots(args.source_root)
    baseline = json.loads(args.baseline_report.read_text(encoding="utf-8"))
    expected = baseline.get("file_sha256", {})
    if not isinstance(expected, dict) or not expected:
        parser.error("baseline-report 缺少 file_sha256")
    actual: dict[str, str | None] = {}
    for relative in sorted(expected):
        if relative.startswith("equipment_patch/"):
            if roots.equipment_patch_root is None:
                path = None
            else:
                path = roots.equipment_patch_root / relative.removeprefix(
                    "equipment_patch/"
                )
        else:
            path = roots.delivery_root / relative
        actual[relative] = _hash(path) if path is not None and path.is_file() else None
    missing = sorted(name for name, digest in actual.items() if digest is None)
    changed = sorted(
        name
        for name, digest in actual.items()
        if digest is not None and digest != expected[name]
    )
    payload = {
        "source_root": str(roots.scope_root),
        "baseline_report": str(args.baseline_report.resolve()),
        "file_count": len(expected),
        "all_hashes_unchanged": not missing and not changed,
        "missing_files": missing,
        "changed_files": changed,
        "actual_sha256": actual,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["all_hashes_unchanged"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
