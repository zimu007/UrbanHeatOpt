"""Build four auditable UrbanHeatOpt competition delivery archives.

The builder is deliberately whitelist-only.  It never deletes repository data,
never follows symbolic links and never packages the project ``IN_DATA`` tree.
Source can be read from an immutable Git commit or from a clean export directory.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
from typing import Mapping, Sequence
import zipfile


SCHEMA_VERSION = "urbanheatopt_competition_release/1.0"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

SOURCE_ROOT_FILES = {
    "README.md",
    "LICENSE",
    "CITATION.cff",
    "pyproject.toml",
    "environment.yml",
    "run.py",
}
SOURCE_PREFIXES = (
    "src/urbanheatopt/",
    "configs/",
    "scripts/",
    "baselines/spatial/guanggu_osm_20260828/",
    "docs/architecture/",
    "docs/model/",
    "docs/runbooks/",
    "docs/decisions/",
    "docs/team/",
)
SOURCE_EXCLUDED_PREFIXES = (
    "src/urbanheatopt/gui/",
    "src/urbanheatopt/data/adapters/legacy_case.py",
    "configs/legacy/",
    "docs/decisions/history/",
)
SOURCE_TEST_FILES = {
    "tests/test_a_integration.py",
    "tests/test_b1_handoff_adapter.py",
    "tests/test_b2_site_capacity.py",
    "tests/test_b3_mode_diagnostics.py",
    "tests/test_b4_tes_research.py",
    "tests/test_b5_paired_tasks.py",
    "tests/test_capacity_margin_basis.py",
    "tests/test_competition_release_builder.py",
    "tests/test_full_study_reporting.py",
    "tests/test_road_v2_compact_tes.py",
    "tests/test_road_v2_core.py",
    "tests/test_solve_request_executor.py",
    "tests/test_solver_parallel_execution.py",
    "tests/test_tes_export_semantics.py",
    "tests/test_v2_freeze_0907.py",
}
SOURCE_TOOL_FILES = {
    "tools/build_competition_release.py",
    "tools/check_environment.py",
    "tools/verify_source_hashes.py",
    "docs/CHANGELOG_COMPETITION.md",
}
SOURCE_SUFFIXES = {
    ".py",
    ".json",
    ".yaml",
    ".yml",
    ".md",
    ".txt",
    ".csv",
    ".toml",
    ".cff",
    ".cmd",
    ".bat",
    ".sh",
}
EXAMPLE_SUFFIXES = {
    ".csv",
    ".parquet",
    ".geojson",
    ".yaml",
    ".yml",
    ".json",
    ".md",
    ".txt",
}
RESULT_EVIDENCE_NAMES = {
    "request_set_summary.json",
    "pareto_points.csv",
    "pareto_frontiers.json",
    "knee_points.json",
    "run_manifest.json",
    "result_bundle.json",
    "solver_evidence.json",
    "candidate_site_comparison.csv",
    "selected_site.json",
    "capacity_decisions.csv",
    "building_connection.csv",
    "station_decisions.csv",
    "storage_decisions.csv",
    "network_decisions.csv",
    "network_decisions.geojson",
    "access_decisions.csv",
    "access_decisions.geojson",
    "cost_breakdown.csv",
    "carbon_breakdown.csv",
    "independent_qa.json",
    "independent_recalculation.json",
    "qa_summary.json",
    "solution_summary.json",
    "tes_pair_qa.json",
    "fixed_structure.json",
    "tes_on_request.json",
    "tes_sensitivity_summary.json",
    "tes_sensitivity_summary.csv",
    "tes_sensitivity_comparison.json",
}
REPRESENTATIVE_HOURLY_NAMES = {
    "building_hourly.parquet",
    "dispatch_hourly.parquet",
    "network_hourly.parquet",
    "storage_hourly.parquet",
    "node_balance_check.parquet",
}
FIGURE_SUFFIXES = {".png", ".svg", ".pdf", ".pptx", ".xlsx", ".csv", ".json", ".md"}
FORBIDDEN_INPUT_PARTS = {"in_data", "raw", "原始输入数据"}
FORBIDDEN_RESULT_PARTS = {"candidate_tasks", "selected_solution"}


class ReleaseBuildError(ValueError):
    """A safe, user-correctable release input error."""


@dataclass(frozen=True)
class ArchiveMember:
    path: str
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def digest(self) -> str:
        return sha256(self.data).hexdigest()


@dataclass(frozen=True)
class PackagePlan:
    key: str
    filename: str
    members: tuple[ArchiveMember, ...]
    scope_note: str

    @property
    def payload_size(self) -> int:
        return sum(item.size for item in self.members)


def _normalise_relative(path: str | Path) -> str:
    value = PurePosixPath(str(path).replace("\\", "/"))
    if value.is_absolute() or ".." in value.parts or not value.parts:
        raise ReleaseBuildError(f"非法归档相对路径: {path}")
    return value.as_posix()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run_git(repo_root: Path, *args: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo_root), *args], stderr=subprocess.STDOUT
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "output", b"").decode("utf-8", errors="replace")
        raise ReleaseBuildError(f"Git命令失败: {' '.join(args)}\n{detail}") from exc


def _is_source_path(relative: str) -> bool:
    path = PurePosixPath(relative)
    suffix = path.suffix.lower()
    if relative.startswith(SOURCE_EXCLUDED_PREFIXES):
        return False
    if relative in SOURCE_ROOT_FILES or relative in SOURCE_TOOL_FILES:
        return suffix in SOURCE_SUFFIXES
    if relative.startswith("tests/"):
        return relative in SOURCE_TEST_FILES
    return relative.startswith(SOURCE_PREFIXES) and suffix in SOURCE_SUFFIXES


def collect_source_from_git(repo_root: Path, git_ref: str) -> tuple[list[ArchiveMember], dict[str, str]]:
    repo_root = repo_root.resolve()
    if not repo_root.is_dir():
        raise ReleaseBuildError(f"代码仓库不存在: {repo_root}")
    commit = _run_git(repo_root, "rev-parse", "--verify", f"{git_ref}^{{commit}}")
    commit_sha = commit.decode("ascii").strip()
    listing = _run_git(repo_root, "ls-tree", "-r", "--name-only", "-z", commit_sha)
    paths = [item.decode("utf-8") for item in listing.split(b"\0") if item]
    selected = sorted(path for path in paths if _is_source_path(path))
    if not selected:
        raise ReleaseBuildError(f"Git引用 {git_ref!r} 中没有命中源码白名单")
    members = [
        ArchiveMember(_normalise_relative(path), _run_git(repo_root, "show", f"{commit_sha}:{path}"))
        for path in selected
    ]
    return members, {"source_mode": "git_ref", "git_ref": git_ref, "git_sha": commit_sha}


def _directory_is_git_root(path: Path) -> bool:
    try:
        root = _run_git(path, "rev-parse", "--show-toplevel").decode("utf-8").strip()
    except ReleaseBuildError:
        return False
    return Path(root).resolve() == path.resolve()


def collect_source_from_directory(source_dir: Path) -> tuple[list[ArchiveMember], dict[str, str]]:
    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise ReleaseBuildError(f"源码目录不存在: {source_dir}")
    if _directory_is_git_root(source_dir):
        dirty = _run_git(source_dir, "status", "--porcelain", "--untracked-files=all")
        if dirty.strip():
            raise ReleaseBuildError("--source-dir 指向的Git工作树不干净；请改用 --git-ref 或先生成干净导出目录")
    members: list[ArchiveMember] = []
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.is_symlink():
            raise ReleaseBuildError(f"源码白名单内不允许符号链接: {path}")
        relative = path.relative_to(source_dir).as_posix()
        if _is_source_path(relative):
            members.append(ArchiveMember(relative, path.read_bytes()))
    if not members:
        raise ReleaseBuildError("源码目录没有命中白名单")
    return members, {"source_mode": "clean_directory", "source_dir": str(source_dir)}


def _collect_whitelisted_directory(
    root: Path,
    *,
    allowed_suffixes: set[str] | None = None,
    allowed_names: set[str] | None = None,
    forbidden_parts: set[str] | None = None,
    max_file_bytes: int,
    max_total_bytes: int,
) -> list[ArchiveMember]:
    root = root.resolve()
    if not root.is_dir():
        raise ReleaseBuildError(f"交付输入目录不存在: {root}")
    members: list[ArchiveMember] = []
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative_path = path.relative_to(root)
        relative = _normalise_relative(relative_path)
        folded_parts = {part.casefold() for part in relative_path.parts}
        if forbidden_parts and folded_parts.intersection({part.casefold() for part in forbidden_parts}):
            continue
        if allowed_names is not None and path.name not in allowed_names:
            continue
        if allowed_suffixes is not None and path.suffix.lower() not in allowed_suffixes:
            continue
        if path.is_symlink():
            raise ReleaseBuildError(f"交付包不允许符号链接: {path}")
        size = path.stat().st_size
        if size > max_file_bytes:
            raise ReleaseBuildError(f"单文件超过白名单包限制({max_file_bytes} bytes): {path}")
        total += size
        if total > max_total_bytes:
            raise ReleaseBuildError(f"白名单包总量超过限制({max_total_bytes} bytes): {root}")
        members.append(ArchiveMember(relative, path.read_bytes()))
    return members


def collect_example_input(root: Path) -> list[ArchiveMember]:
    resolved_parts = {part.casefold() for part in root.resolve().parts}
    if resolved_parts.intersection(FORBIDDEN_INPUT_PARTS):
        raise ReleaseBuildError("示例输入不得直接取自IN_DATA/raw/原始输入数据目录")
    members = _collect_whitelisted_directory(
        root,
        allowed_suffixes=EXAMPLE_SUFFIXES,
        max_file_bytes=20 * 1024 * 1024,
        max_total_bytes=100 * 1024 * 1024,
    )
    if not members:
        raise ReleaseBuildError("示例输入目录没有可交付文件")
    return members


def _representative_hourly_members(
    *,
    source_root: Path,
    result_directory: Path,
    archive_prefix: str = "",
) -> list[ArchiveMember]:
    source_root = source_root.resolve()
    result_directory = result_directory.resolve()
    try:
        result_directory.relative_to(source_root)
    except ValueError as exc:
        raise ReleaseBuildError("代表方案目录越出结果根目录") from exc
    members: list[ArchiveMember] = []
    for filename in sorted(REPRESENTATIVE_HOURLY_NAMES):
        path = result_directory / filename
        if not path.is_file():
            raise ReleaseBuildError(f"代表方案缺少逐时证据: {path}")
        if path.stat().st_size > 30 * 1024 * 1024:
            raise ReleaseBuildError(f"代表方案逐时文件超过30MiB: {path}")
        _validate_hourly_parquet(path, filename)
        relative = path.relative_to(source_root).as_posix()
        members.append(
            ArchiveMember(_normalise_relative(f"{archive_prefix}{relative}"), path.read_bytes())
        )
    return members


def _load_json(path: Path, *, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseBuildError(f"{label}无法读取为有效JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ReleaseBuildError(f"{label}必须是JSON对象: {path}")
    return payload


def _validate_hourly_parquet(path: Path, filename: str) -> None:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - environment gate catches this first
        raise ReleaseBuildError("校验逐时Parquet需要已冻结依赖pyarrow") from exc
    required = {
        "building_hourly.parquet": {"building_id", "hour", "timestamp", "demand_kW"},
        "dispatch_hourly.parquet": {"location_id", "technology_id", "hour", "timestamp", "heat_kW_th"},
        "network_hourly.parquet": {"edge_id", "hour", "timestamp", "signed_flow_kW_th"},
        "storage_hourly.parquet": {"site_id", "hour", "timestamp", "charge_kW", "discharge_kW", "soc_kWh"},
        "node_balance_check.parquet": {"node_id", "hour", "residual_kW"},
    }[filename]
    try:
        parquet = pq.ParquetFile(path)
        columns = set(parquet.schema_arrow.names)
        table = pq.read_table(path, columns=["hour"])
    except Exception as exc:
        raise ReleaseBuildError(f"代表方案逐时Parquet损坏或不可读: {path}") from exc
    missing = sorted(required - columns)
    if missing:
        raise ReleaseBuildError(f"代表方案逐时Parquet缺少字段{missing}: {path}")
    if parquet.metadata.num_rows <= 0:
        raise ReleaseBuildError(f"代表方案逐时Parquet为空: {path}")
    hours = table.column("hour").to_pylist()
    unique_hours = sorted({int(value) for value in hours if value is not None})
    if unique_hours != list(range(1, 2161)):
        raise ReleaseBuildError(f"代表方案逐时Parquet未覆盖模型小时1..2160: {path}")


def _named_members(
    *,
    source_root: Path,
    directory: Path,
    archive_prefix: str = "",
) -> list[ArchiveMember]:
    source_root = source_root.resolve()
    directory = directory.resolve()
    try:
        directory.relative_to(source_root)
    except ValueError as exc:
        raise ReleaseBuildError("结果证据目录越出结果根目录") from exc
    members: list[ArchiveMember] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name not in RESULT_EVIDENCE_NAMES:
            continue
        if path.is_symlink():
            raise ReleaseBuildError(f"结果证据不允许符号链接: {path}")
        if path.stat().st_size > 30 * 1024 * 1024:
            raise ReleaseBuildError(f"结果证据文件超过30MiB: {path}")
        relative = path.relative_to(source_root).as_posix()
        members.append(ArchiveMember(_normalise_relative(f"{archive_prefix}{relative}"), path.read_bytes()))
    return members


def _qualified_result(directory: Path, *, label: str) -> None:
    bundle_path = directory / "result_bundle.json"
    qa_path = directory / "independent_qa.json"
    if not bundle_path.is_file() or not qa_path.is_file():
        raise ReleaseBuildError(f"{label}缺少result_bundle.json或independent_qa.json")
    bundle = _load_json(bundle_path, label=f"{label}结果包")
    qa = _load_json(qa_path, label=f"{label}独立QA")
    if bundle.get("qualified") is not True or bundle.get("solver_executed") is not True:
        raise ReleaseBuildError(f"{label}结果未通过qualified/solver_executed门禁")
    if str(bundle.get("termination_condition", "")).casefold() != "optimal":
        raise ReleaseBuildError(f"{label}求解状态不是optimal")
    if qa.get("passed") is not True:
        raise ReleaseBuildError(f"{label}独立QA未通过")
    solve_evidence = bundle.get("solve_evidence")
    gap = solve_evidence.get("certified_gap") if isinstance(solve_evidence, dict) else None
    if not isinstance(gap, (int, float)) or float(gap) > 0.01:
        raise ReleaseBuildError(f"{label}缺少不超过1%的认证gap")


def _resolve_selected_sensitivity(summary: Mapping[str, object], root: Path) -> Path:
    attempts = summary.get("attempts")
    selected_multiplier = summary.get("selected_multiplier")
    selected_scenario: str | None = None
    if isinstance(attempts, list):
        for row in attempts:
            if not isinstance(row, dict):
                continue
            if (
                row.get("tes_used") is True
                and row.get("qualified") is True
                and row.get("pair_qa_passed") is True
                and row.get("tes_capex_multiplier") == selected_multiplier
            ):
                selected_scenario = str(row.get("scenario_id", ""))
                break
    raw_path = str(summary.get("selected_result_path", "")).strip()
    candidates: list[Path] = []
    if selected_scenario:
        candidates.append(root / selected_scenario)
    if raw_path:
        raw = Path(raw_path)
        candidates.append(raw if raw.is_absolute() else root / raw)
        candidates.append(root / raw.name)
    for candidate in candidates:
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        if resolved.is_dir():
            return resolved
    raise ReleaseBuildError("TES敏感性选中目录不存在或越出敏感性结果根目录")


def collect_result_evidence(
    root: Path,
    tes_sensitivity_root: Path | None = None,
) -> list[ArchiveMember]:
    root = root.resolve()
    summary_path = root / "request_set_summary.json"
    if not summary_path.is_file():
        raise ReleaseBuildError("结果证据缺少 request_set_summary.json")
    root_summary = _load_json(summary_path, label="全季汇总")
    pareto_summary = root_summary.get("pareto")
    if root_summary.get("qualified") is not True or not isinstance(pareto_summary, dict) or pareto_summary.get("qualified") is not True:
        raise ReleaseBuildError("全季汇总或Pareto汇总未通过qualified门禁")
    members = _named_members(source_root=root, directory=root)
    pareto_root = root / "pareto_no_tes"
    members.extend(_named_members(source_root=root, directory=pareto_root))
    knees_path = root / "pareto_no_tes" / "knee_points.json"
    if not knees_path.is_file():
        raise ReleaseBuildError("结果证据缺少pareto_no_tes/knee_points.json")
    knees = _load_json(knees_path, label="膝点记录")
    combined = knees.get("combined")
    if not isinstance(combined, dict) or not combined.get("point_id"):
        raise ReleaseBuildError("knee_points.json缺少combined.point_id")
    point_id = str(combined["point_id"])
    if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in point_id):
        raise ReleaseBuildError("knee_points.json包含非法point_id")
    point_directory = root / "pareto_no_tes" / "points" / point_id
    _qualified_result(point_directory, label="权威膝点")
    members.extend(_named_members(source_root=root, directory=point_directory))
    members.extend(
        _representative_hourly_members(
            source_root=root,
            result_directory=point_directory,
        )
    )

    for mode in ("central", "hybrid"):
        pair_directory = root / "tes_pairs" / mode
        if pair_directory.is_dir():
            members.extend(_named_members(source_root=root, directory=pair_directory))

    if tes_sensitivity_root is not None:
        sensitivity_root = tes_sensitivity_root.resolve()
        summary_path = sensitivity_root / "tes_sensitivity_summary.json"
        if not summary_path.is_file():
            raise ReleaseBuildError("TES敏感性目录缺少tes_sensitivity_summary.json")
        summary = _load_json(summary_path, label="TES敏感性汇总")
        if summary.get("demonstration_achieved") is not True or summary.get("all_attempted_results_qualified") is not True:
            raise ReleaseBuildError("TES敏感性尚未形成实际充放热证据")
        selected = _resolve_selected_sensitivity(summary, sensitivity_root)
        _qualified_result(selected, label="TES敏感性选中方案")
        selected_decisions = selected / "storage_decisions.csv"
        if not selected_decisions.is_file():
            raise ReleaseBuildError("TES敏感性选中方案缺少storage_decisions.csv")
        sensitivity_members = _named_members(source_root=sensitivity_root, directory=sensitivity_root)
        sensitivity_members.extend(_named_members(source_root=sensitivity_root, directory=selected))
        members.extend(
            ArchiveMember(f"tes_sensitivity/{item.path}", item.data)
            for item in sensitivity_members
        )
        members.extend(
            _representative_hourly_members(
                source_root=sensitivity_root,
                result_directory=selected,
                archive_prefix="tes_sensitivity/",
            )
        )
    duplicates = [path for path in {item.path for item in members} if sum(member.path == path for member in members) > 1]
    if duplicates:
        raise ReleaseBuildError(f"结果证据归档路径重复: {sorted(duplicates)}")
    return members


def collect_figure_atlas(root: Path) -> list[ArchiveMember]:
    resolved_parts = {part.casefold() for part in root.resolve().parts}
    if resolved_parts.intersection(FORBIDDEN_INPUT_PARTS):
        raise ReleaseBuildError("图册目录不得直接取自IN_DATA/raw/原始输入数据目录")
    members = _collect_whitelisted_directory(
        root,
        allowed_suffixes=FIGURE_SUFFIXES,
        forbidden_parts={"workbook_previews"},
        max_file_bytes=50 * 1024 * 1024,
        max_total_bytes=500 * 1024 * 1024,
    )
    if not any(PurePosixPath(item.path).suffix.lower() in {".png", ".svg", ".pdf", ".pptx"} for item in members):
        raise ReleaseBuildError("图册目录至少需要一份PNG/SVG/PDF/PPTX可视成果")
    return members


def _package_manifest(plan: PackagePlan) -> bytes:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "package": plan.key,
        "scope_note": plan.scope_note,
        "files": [
            {"path": member.path, "size_bytes": member.size, "sha256": member.digest}
            for member in plan.members
        ],
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_deterministic_zip(path: Path, plan: PackagePlan) -> None:
    manifest = ArchiveMember("_PACKAGE_MANIFEST.json", _package_manifest(plan))
    members = sorted((*plan.members, manifest), key=lambda item: item.path)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for member in members:
            info = zipfile.ZipInfo(member.path, date_time=FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, member.data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def make_release_plan(
    *,
    repo_root: Path,
    git_ref: str | None,
    source_dir: Path | None,
    example_input: Path,
    result_root: Path,
    figure_root: Path,
    tes_sensitivity_root: Path | None = None,
) -> tuple[list[PackagePlan], dict[str, str]]:
    if git_ref and source_dir:
        raise ReleaseBuildError("--git-ref 与 --source-dir 只能选择一个")
    if source_dir is not None:
        source_members, source_identity = collect_source_from_directory(source_dir)
    else:
        source_members, source_identity = collect_source_from_git(repo_root, git_ref or "HEAD")
    plans = [
        PackagePlan(
            "source_code",
            "01_source_code.zip",
            tuple(source_members),
            "竞赛主线源码、配置、入口、测试源码及最小复现工具；不含原始数据和运行结果。",
        ),
        PackagePlan(
            "example_input",
            "02_example_input.zip",
            tuple(collect_example_input(example_input)),
            "可公开的小型示例输入；禁止从项目原始IN_DATA直接取样。",
        ),
        PackagePlan(
            "result_evidence",
            "03_result_evidence.zip",
            tuple(collect_result_evidence(result_root, tes_sensitivity_root)),
            "紧凑结果与独立QA证据；仅保留权威膝点及TES敏感性选中方案的逐时Parquet，不含重复候选解和原始日志。",
        ),
        PackagePlan(
            "figure_atlas",
            "04_figure_atlas.zip",
            tuple(collect_figure_atlas(figure_root)),
            "用于评审与PPT的渲染图册；图形不能替代数值结果和独立QA。",
        ),
    ]
    return plans, source_identity


def _plan_summary(plans: Sequence[PackagePlan], source_identity: Mapping[str, str]) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_identity": dict(source_identity),
        "packages": [
            {
                "key": plan.key,
                "filename": plan.filename,
                "file_count": len(plan.members),
                "payload_bytes": plan.payload_size,
                "scope_note": plan.scope_note,
            }
            for plan in plans
        ],
    }


def build_release(
    output_root: Path,
    plans: Sequence[PackagePlan],
    source_identity: Mapping[str, str],
) -> dict[str, object]:
    output_root = output_root.resolve()
    if output_root.exists():
        raise ReleaseBuildError(f"输出目录已存在，拒绝覆盖: {output_root}")
    output_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_root.name}.building-", dir=output_root.parent))
    package_rows: list[dict[str, object]] = []
    try:
        for plan in plans:
            package_path = staging / plan.filename
            _write_deterministic_zip(package_path, plan)
            package_rows.append(
                {
                    "key": plan.key,
                    "filename": plan.filename,
                    "file_count": len(plan.members),
                    "payload_bytes": plan.payload_size,
                    "archive_bytes": package_path.stat().st_size,
                    "sha256": _sha256_file(package_path),
                    "scope_note": plan.scope_note,
                }
            )
        index = {
            "schema_version": SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_identity": dict(source_identity),
            "packages": package_rows,
        }
        (staging / "release_index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (staging / "SHA256SUMS.txt").write_text(
            "".join(f"{row['sha256']}  {row['filename']}\n" for row in package_rows),
            encoding="utf-8",
        )
        lines = [
            "# UrbanHeatOpt 竞赛交付索引",
            "",
            f"- 交付规范：`{SCHEMA_VERSION}`",
            f"- 源码模式：`{source_identity.get('source_mode', 'unknown')}`",
        ]
        if "git_sha" in source_identity:
            lines.append(f"- Git SHA：`{source_identity['git_sha']}`")
        lines.extend(["", "| 文件 | 内容 | 文件数 | SHA-256 |", "|---|---|---:|---|"])
        for row in package_rows:
            lines.append(
                f"| `{row['filename']}` | {row['scope_note']} | {row['file_count']} | `{row['sha256']}` |"
            )
        lines.extend(
            [
                "",
                "> 本目录由白名单工具生成，不含项目原始大数据；结果证据包也不以图片替代独立QA。",
                "",
            ]
        )
        (staging / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
        os.replace(staging, output_root)
        return index
    except Exception:
        # Do not recursively delete anything after a failed build.  The uniquely
        # named staging directory is intentionally retained for diagnosis.
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--git-ref", help="从不可变Git引用提取源码（默认HEAD）")
    source.add_argument("--source-dir", type=Path, help="从干净导出目录提取源码")
    parser.add_argument("--example-input", required=True, type=Path)
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument("--tes-sensitivity-root", type=Path)
    parser.add_argument("--figure-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true", help="只验证并打印白名单计划，不写文件")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        plans, source_identity = make_release_plan(
            repo_root=args.repo_root,
            git_ref=args.git_ref,
            source_dir=args.source_dir,
            example_input=args.example_input,
            result_root=args.result_root,
            figure_root=args.figure_root,
            tes_sensitivity_root=args.tes_sensitivity_root,
        )
        if args.dry_run:
            print(json.dumps(_plan_summary(plans, source_identity), ensure_ascii=False, indent=2))
            return 0
        result = build_release(args.output_root, plans, source_identity)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ReleaseBuildError as exc:
        print(f"交付包生成失败: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
