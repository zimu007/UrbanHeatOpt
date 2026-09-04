"""Verify the immutable Compact full-season V1/R3 result and source tags."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    REPOSITORY
    / "docs"
    / "releases"
    / "compact_fullseason_v1_r3"
    / "manifest.json"
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def result_tree_digest(root: Path) -> tuple[int, int, str]:
    """Return count, bytes and the manifest-defined deterministic tree hash."""

    files = sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    digest = hashlib.sha256()
    total_bytes = 0
    for path in files:
        size = path.stat().st_size
        relative = path.relative_to(root).as_posix()
        digest.update(
            f"{relative}\t{size}\t{file_sha256(path)}\n".encode("utf-8")
        )
        total_bytes += size
    return len(files), total_bytes, digest.hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return payload


def resolve_tag(tag: str) -> str | None:
    process = subprocess.run(
        ["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"],
        cwd=REPOSITORY,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return process.stdout.strip() if process.returncode == 0 else None


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    result.add_argument(
        "--result-root",
        type=Path,
        default=(
            REPOSITORY
            / "runs"
            / "road_joint_v2"
            / "COMPACT_FULLSEASON_V1_20260903_R3"
        ),
    )
    result.add_argument("--archive", type=Path)
    result.add_argument(
        "--skip-tags",
        action="store_true",
        help="verify artifacts only when Git metadata is intentionally unavailable",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    manifest_path = args.manifest.expanduser().resolve()
    root = args.result_root.expanduser().resolve()
    manifest = read_object(manifest_path)
    errors: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    repository = manifest["repository"]
    if not args.skip_tags:
        check(
            resolve_tag(repository["source_tag"])
            == repository["executed_commit"],
            "source tag does not resolve to the executed R3 commit",
        )
        check(
            resolve_tag(repository["unlimited_runtime_successor_tag"])
            == repository["unlimited_runtime_successor_commit"],
            "unlimited-runtime tag does not resolve to its recorded commit",
        )

    artifacts = manifest["result_artifacts"]
    check(root.is_dir(), f"result root is missing: {root}")
    check(root.name == artifacts["result_root_name"], "unexpected result-root name")
    if root.is_dir():
        for relative, expected in artifacts["core_files_sha256"].items():
            path = root / relative
            check(path.is_file(), f"missing core result file: {relative}")
            if path.is_file():
                check(
                    file_sha256(path) == expected,
                    f"SHA-256 mismatch: {relative}",
                )

        count, total_bytes, tree_hash = result_tree_digest(root)
        check(count == artifacts["file_count"], "result file count changed")
        check(total_bytes == artifacts["total_bytes"], "result byte count changed")
        check(
            tree_hash == artifacts["tree_digest"]["sha256"],
            "result tree digest changed",
        )

        completion = read_object(root / "completion_manifest.json")
        check(completion.get("status") == "complete", "completion status is not complete")
        check(
            completion.get("certified_point_count")
            == manifest["scope"]["certified_scan_points"],
            "certified scan-point count changed",
        )
        check(
            completion.get("combined_frontier_point_count")
            == manifest["scope"]["combined_frontier_points"],
            "combined frontier count changed",
        )
        check(
            completion.get("final_replay_complete") is True
            and completion.get("final_replay_point_count")
            == manifest["scope"]["final_fixed_decision_replays"],
            "final replay completion changed",
        )
        for relative, expected in completion.get("success_marker_sha256", {}).items():
            path = root / relative
            check(path.is_file(), f"missing scan certificate: {relative}")
            if path.is_file():
                check(file_sha256(path) == expected, f"scan certificate changed: {relative}")

        result = read_object(root / "compact_pareto_frontiers.json")
        expected_ids = [point["point_id"] for point in manifest["frontier"]]
        actual_ids = [point["point_id"] for point in result.get("combined_frontier", [])]
        check(actual_ids == expected_ids, "frontier point ordering or identity changed")
        replays = result.get("final_fixed_decision_replays", {})
        check(set(replays) == set(expected_ids), "final replay identity set changed")
        for point_id, replay in replays.items():
            check(replay.get("continuous_replay") is True, f"{point_id}: not a continuous replay")
            check(replay.get("unfixed_integer_count") == 0, f"{point_id}: integer decisions are not fixed")
            check(replay.get("qa", {}).get("passed") is True, f"{point_id}: replay QA failed")

    if args.archive is not None:
        archive = args.archive.expanduser().resolve()
        expected_archive = artifacts["archive"]
        check(archive.is_file(), f"archive is missing: {archive}")
        if archive.is_file():
            check(archive.stat().st_size == expected_archive["bytes"], "archive size changed")
            check(file_sha256(archive) == expected_archive["sha256"], "archive SHA-256 changed")

    report = {
        "baseline_id": manifest["baseline_id"],
        "manifest": str(manifest_path),
        "result_root": str(root),
        "archive_checked": args.archive is not None,
        "passed": not errors,
        "errors": errors,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
