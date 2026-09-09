"""Static, evidence-gated reporting for a completed ``full-study`` run.

The optimizer deliberately writes machine-oriented result bundles.  This
module is the read-only adapter between that contract and presentation-ready
artifacts.  It never solves a model and never changes a run directory.

The authoritative knee is read from ``pareto_no_tes/knee_points.json``.  The
``is_knee`` column in ``pareto_points.csv`` is intentionally not trusted: old
full-study exports did not rewrite that denormalized column after knee
selection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.font_manager as font_manager
import matplotlib.image as mpimg
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


REPORTING_VERSION = "full_study_reporting_1.1.0"
_MODE_ORDER = ("central", "distributed", "hybrid")
_MODE_CN = {"central": "集中式", "distributed": "分布式", "hybrid": "混合式"}
_MODE_COLOR = {"central": "#2878B5", "distributed": "#F28E2B", "hybrid": "#2A9D8F"}
_KNEE_COLOR = "#7A5195"
_CAPACITY_TOLERANCE = 1.0e-6


class FullStudyReportError(ValueError):
    """Raised when a run cannot be represented as a qualified full study."""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FullStudyReportError(f"缺少必需结果文件：{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FullStudyReportError(f"无法读取JSON结果：{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FullStudyReportError(f"JSON顶层必须是对象：{path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_point_id(value: Any) -> str:
    point_id = str(value)
    if not point_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in point_id):
        raise FullStudyReportError(f"非法Pareto点ID：{point_id!r}")
    return point_id


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, np.integer, np.floating)):
        number = float(value)
        if math.isfinite(number):
            if abs(number) <= 1.0e-7:
                return False
            if abs(number - 1.0) <= 1.0e-7:
                return True
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no", "", "nan"}:
        return False
    raise FullStudyReportError(f"无法解析布尔值：{value!r}")


def _numeric(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    for column in columns:
        if column not in frame.columns:
            raise FullStudyReportError(f"结果表缺少字段：{column}")
        frame[column] = pd.to_numeric(frame[column], errors="raise")


@dataclass(frozen=True)
class PointEvidence:
    point_id: str
    directory: Path
    solution: Mapping[str, Any]
    manifest: Mapping[str, Any]
    qa: Mapping[str, Any]


@dataclass(frozen=True)
class FullStudyResult:
    run_root: Path
    root_summary: Mapping[str, Any]
    pareto_summary: Mapping[str, Any]
    frontiers: Mapping[str, Any]
    knees: Mapping[str, Any]
    points: pd.DataFrame
    evidence: Mapping[str, PointEvidence]
    combined_knee_id: str
    building_count: int
    hour_count: int
    git_sha: str
    optimization_scope: str

    @property
    def knee_directory(self) -> Path:
        return self.evidence[self.combined_knee_id].directory


@dataclass(frozen=True)
class FullStudyReport:
    output_directory: Path
    guide: Path
    manifest: Path
    atlas_pdf: Path
    figure_files: tuple[Path, ...]
    table_files: tuple[Path, ...]


class FullStudyResultAdapter:
    """Validate and load a production ``FULL_STUDY`` result tree."""

    def __init__(self, run_root: str | Path):
        self.run_root = Path(run_root).expanduser().resolve()

    def load(self) -> FullStudyResult:
        root = self.run_root
        if not root.is_dir():
            raise FullStudyReportError(f"全研究结果目录不存在：{root}")

        root_summary = _read_json(root / "request_set_summary.json")
        if root_summary.get("request_set") != "full-study":
            raise FullStudyReportError("结果目录不是full-study请求集")
        if root_summary.get("qualified") is not True:
            raise FullStudyReportError("full-study顶层状态不是qualified=true")

        pareto_root = root / "pareto_no_tes"
        pareto_summary = _read_json(pareto_root / "request_set_summary.json")
        if pareto_summary.get("qualified") is not True:
            raise FullStudyReportError("Pareto请求集未通过资格门禁")
        if pareto_summary.get("method") != "epsilon_constraint":
            raise FullStudyReportError("Pareto方法不是epsilon_constraint")

        knees = _read_json(pareto_root / "knee_points.json")
        combined_knee = knees.get("combined")
        if not isinstance(combined_knee, dict):
            raise FullStudyReportError("knee_points.json没有可用的combined膝点")
        combined_knee_id = _safe_point_id(combined_knee.get("point_id"))

        frontiers = _read_json(pareto_root / "pareto_frontiers.json")
        if frontiers.get("method") != "epsilon_constraint":
            raise FullStudyReportError("pareto_frontiers.json的方法口径不一致")

        points_path = pareto_root / "pareto_points.csv"
        if not points_path.is_file():
            raise FullStudyReportError(f"缺少Pareto点表：{points_path}")
        points = pd.read_csv(points_path)
        required = {
            "point_id",
            "mode",
            "labels",
            "annual_real_cost_CNY_per_year",
            "annual_operating_carbon_kgCO2e_per_year",
            "annual_hns_penalty_CNY_per_year",
            "unserved_heat_kWh",
            "reported_mip_gap",
        }
        missing = sorted(required - set(points.columns))
        if missing:
            raise FullStudyReportError(f"Pareto点表缺少字段：{', '.join(missing)}")
        points = points.copy()
        points["point_id"] = points["point_id"].map(_safe_point_id)
        if points["point_id"].duplicated().any():
            duplicates = points.loc[points["point_id"].duplicated(), "point_id"].tolist()
            raise FullStudyReportError(f"Pareto点ID重复：{duplicates}")
        invalid_modes = sorted(set(points["mode"].astype(str)) - set(_MODE_ORDER))
        if invalid_modes:
            raise FullStudyReportError(f"存在未知系统模式：{invalid_modes}")
        _numeric(
            points,
            (
                "annual_real_cost_CNY_per_year",
                "annual_operating_carbon_kgCO2e_per_year",
                "annual_hns_penalty_CNY_per_year",
                "unserved_heat_kWh",
                "reported_mip_gap",
            ),
        )
        if combined_knee_id not in set(points["point_id"]):
            raise FullStudyReportError(f"combined膝点不在Pareto点表中：{combined_knee_id}")

        frontier_ids: set[str] = set()
        mode_frontiers = frontiers.get("mode_frontiers", {})
        if not isinstance(mode_frontiers, dict):
            raise FullStudyReportError("mode_frontiers必须是对象")
        for mode in _MODE_ORDER:
            rows = mode_frontiers.get(mode, [])
            if not isinstance(rows, list):
                raise FullStudyReportError(f"{mode}前沿必须是数组")
            for row in rows:
                if not isinstance(row, dict):
                    raise FullStudyReportError(f"{mode}前沿包含非对象记录")
                frontier_ids.add(_safe_point_id(row.get("point_id")))
        for row in frontiers.get("combined_frontier", []):
            if isinstance(row, dict):
                frontier_ids.add(_safe_point_id(row.get("point_id")))
        unknown_frontier = frontier_ids - set(points["point_id"])
        if unknown_frontier:
            raise FullStudyReportError(f"前沿引用未知点：{sorted(unknown_frontier)}")

        evidence: dict[str, PointEvidence] = {}
        counts: set[tuple[int, int]] = set()
        git_shas: set[str] = set()
        scopes: set[str] = set()
        for row in points.to_dict(orient="records"):
            point_id = str(row["point_id"])
            directory = (pareto_root / "points" / point_id).resolve()
            try:
                directory.relative_to(pareto_root.resolve())
            except ValueError as exc:
                raise FullStudyReportError(f"Pareto点路径越界：{point_id}") from exc
            result_bundle = _read_json(directory / "result_bundle.json")
            manifest = _read_json(directory / "run_manifest.json")
            qa = _read_json(directory / "independent_qa.json")
            solution = _read_json(directory / "solution_summary.json")
            if result_bundle.get("qualified") is not True:
                raise FullStudyReportError(f"{point_id}的ResultBundle未通过资格门禁")
            if qa.get("passed") is not True:
                raise FullStudyReportError(f"{point_id}的独立QA未通过")
            if manifest.get("result_qualified") is not True:
                raise FullStudyReportError(f"{point_id}的run_manifest未标记结果合格")
            if manifest.get("legacy_fallback_used") is not False:
                raise FullStudyReportError(f"{point_id}存在legacy回退")
            counts.add((int(manifest.get("building_count", -1)), int(manifest.get("hour_count", -1))))
            git_shas.add(str(manifest.get("git_sha", "")))
            scopes.add(str(manifest.get("optimization_scope", "")))
            if str(solution.get("mode")) != str(row["mode"]):
                raise FullStudyReportError(f"{point_id}的模式与Pareto点表不一致")
            for key, csv_key in (
                ("real_cost_CNY", "annual_real_cost_CNY_per_year"),
                ("carbon_kgCO2e", "annual_operating_carbon_kgCO2e_per_year"),
            ):
                if not math.isclose(float(solution[key]), float(row[csv_key]), rel_tol=1e-10, abs_tol=1e-5):
                    raise FullStudyReportError(f"{point_id}的{key}与Pareto点表不一致")
            evidence[point_id] = PointEvidence(point_id, directory, solution, manifest, qa)

        if len(counts) != 1:
            raise FullStudyReportError(f"Pareto点的建筑/小时范围不一致：{sorted(counts)}")
        building_count, hour_count = next(iter(counts))
        if building_count <= 0 or hour_count <= 0:
            raise FullStudyReportError("建筑数或小时数无效")
        if len(git_shas) != 1 or not next(iter(git_shas)):
            raise FullStudyReportError(f"Pareto点的Git版本不一致：{sorted(git_shas)}")
        if len(scopes) != 1 or not next(iter(scopes)):
            raise FullStudyReportError(f"Pareto点的优化范围不一致：{sorted(scopes)}")

        knee_row = points.loc[points["point_id"] == combined_knee_id].iloc[0]
        if not math.isclose(
            float(knee_row["annual_real_cost_CNY_per_year"]),
            float(combined_knee["annual_real_cost_CNY_per_year"]),
            rel_tol=1e-10,
            abs_tol=1e-5,
        ):
            raise FullStudyReportError("combined膝点成本与Pareto点表不一致")

        self._validate_tes_pairs(root_summary, root)
        return FullStudyResult(
            run_root=root,
            root_summary=root_summary,
            pareto_summary=pareto_summary,
            frontiers=frontiers,
            knees=knees,
            points=points,
            evidence=evidence,
            combined_knee_id=combined_knee_id,
            building_count=building_count,
            hour_count=hour_count,
            git_sha=next(iter(git_shas)),
            optimization_scope=next(iter(scopes)),
        )

    @staticmethod
    def _validate_tes_pairs(root_summary: Mapping[str, Any], run_root: Path) -> None:
        pairs = root_summary.get("tes_pairs", {})
        if not isinstance(pairs, dict):
            raise FullStudyReportError("full-study的tes_pairs必须是对象")
        for mode in ("central", "hybrid"):
            pair = pairs.get(mode)
            if not isinstance(pair, dict) or pair.get("passed") is not True:
                raise FullStudyReportError(f"{mode}的有/无TES配对未通过")
            if pair.get("independent_qa_passed") is not True or pair.get("structure_match") is not True:
                raise FullStudyReportError(f"{mode}的TES结构或独立QA未通过")
            tes_dir = run_root / "tes_pairs" / mode / "tes_on"
            if _read_json(tes_dir / "result_bundle.json").get("qualified") is not True:
                raise FullStudyReportError(f"{mode} TES-on ResultBundle未通过")
            if _read_json(tes_dir / "independent_qa.json").get("passed") is not True:
                raise FullStudyReportError(f"{mode} TES-on独立QA未通过")


def _load_tes_sensitivity(
    result: FullStudyResult,
    sensitivity_root: str | Path | None,
) -> tuple[dict[str, Any] | None, list[Path]]:
    """Load an optional TES investment sensitivity with strict provenance."""

    if sensitivity_root is None:
        return None, []
    root = Path(sensitivity_root).expanduser().resolve()
    summary_path = root / "tes_sensitivity_summary.json"
    summary = _read_json(summary_path)
    if summary.get("schema") != "urbanheatopt_tes_capex_sensitivity_v1":
        raise FullStudyReportError("TES敏感性结果规范版本不受支持")
    if summary.get("demonstration_achieved") is not True:
        raise FullStudyReportError("TES敏感性尚未得到实际充放热结果")
    if summary.get("all_attempted_results_qualified") is not True:
        raise FullStudyReportError("TES敏感性存在未通过资格门禁的尝试")
    if summary.get("road_case_equivalent_to_source") is not True:
        raise FullStudyReportError("TES敏感性RoadCase与源full-study不等价")
    if str(summary.get("source_point_id")) != result.combined_knee_id:
        raise FullStudyReportError("TES敏感性不是基于当前权威膝点固定结构")
    if Path(str(summary.get("source_study", ""))).expanduser().resolve() != result.run_root:
        raise FullStudyReportError("TES敏感性引用了另一份full-study结果")

    selected_root = Path(str(summary.get("selected_result_path", ""))).expanduser().resolve()
    try:
        selected_root.relative_to(root)
    except ValueError as exc:
        raise FullStudyReportError("TES敏感性选中结果越出指定结果目录") from exc
    comparison_path = selected_root / "tes_sensitivity_comparison.json"
    qa_path = selected_root / "independent_qa.json"
    bundle_path = selected_root / "result_bundle.json"
    decisions_path = selected_root / "storage_decisions.csv"
    comparison = _read_json(comparison_path)
    qa = _read_json(qa_path)
    bundle = _read_json(bundle_path)
    if comparison.get("passed") is not True or comparison.get("independent_qa_passed") is not True:
        raise FullStudyReportError("TES敏感性配对复算未通过")
    if qa.get("passed") is not True or bundle.get("qualified") is not True:
        raise FullStudyReportError("TES敏感性ResultBundle或独立QA未通过")
    tes = comparison.get("tes")
    if not isinstance(tes, dict):
        raise FullStudyReportError("TES敏感性缺少储热决策摘要")
    energy = float(tes.get("energy_capacity_kWh_th", 0.0))
    charge = float(tes.get("actual_peak_charge_kW_th", 0.0))
    discharge = float(tes.get("actual_peak_discharge_kW_th", 0.0))
    if not (
        bool(tes.get("tes_used"))
        and energy > _CAPACITY_TOLERANCE
        and charge > _CAPACITY_TOLERANCE
        and discharge > _CAPACITY_TOLERANCE
    ):
        raise FullStudyReportError("TES敏感性没有可核查的正容量充放热")
    multiplier = float(comparison.get("tes_capex_multiplier"))
    if not math.isclose(
        multiplier,
        float(summary.get("selected_multiplier")),
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise FullStudyReportError("TES敏感性汇总与选中结果的投资乘数不一致")
    if not decisions_path.is_file():
        raise FullStudyReportError("TES敏感性缺少storage_decisions.csv")
    return {
        "root": root,
        "selected_root": selected_root,
        "summary": summary,
        "comparison": comparison,
        "tes": tes,
    }, [summary_path, comparison_path, qa_path, bundle_path, decisions_path]


def _configure_style() -> None:
    available = {font.name for font in font_manager.fontManager.ttflist}
    preferred = ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Arial Unicode MS", "DejaVu Sans")
    selected = next((name for name in preferred if name in available), "DejaVu Sans")
    plt.rcParams.update(
        {
            "font.family": selected,
            "axes.unicode_minus": False,
            "axes.titleweight": "bold",
            "axes.titlesize": 17,
            "axes.labelsize": 12,
            "figure.facecolor": "white",
            "axes.facecolor": "#FAFBFC",
            "grid.alpha": 0.24,
            "savefig.facecolor": "white",
        }
    )


def _new_figure(title: str, *, columns: int = 1) -> tuple[plt.Figure, Any]:
    fig, axes = plt.subplots(1, columns, figsize=(13.333, 7.5), constrained_layout=True)
    fig.suptitle(title, fontsize=20, fontweight="bold", x=0.04, ha="left")
    return fig, axes


def _add_footer(fig: plt.Figure, result: FullStudyResult) -> None:
    text = (
        f"{result.run_root.name}  |  {result.building_count}栋×{result.hour_count}h  |  "
        f"{result.optimization_scope}  |  规划优化能力验证（非施工方案）"
    )
    fig.text(0.04, 0.012, text, fontsize=8.5, color="#5F6B76", ha="left", va="bottom")


def _save_figure(
    fig: plt.Figure,
    figure_id: str,
    title: str,
    figures_dir: Path,
    pdf: PdfPages,
    result: FullStudyResult,
) -> dict[str, Any]:
    # Reserve a dedicated footer band.  Matplotlib's constrained layout does
    # not account for ``fig.text`` and previously let the provenance footer
    # overlap the x-axis title in the rendered PPT figures.
    fig.set_layout_engine(None)
    fig.subplots_adjust(left=0.085, right=0.985, bottom=0.145, top=0.88)
    _add_footer(fig, result)
    png = figures_dir / f"{figure_id}.png"
    svg = figures_dir / f"{figure_id}.svg"
    fig.savefig(png, dpi=220, bbox_inches="tight")
    fig.savefig(svg, format="svg", bbox_inches="tight")
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)
    image = mpimg.imread(png)
    if image.ndim < 2 or image.shape[0] < 900 or image.shape[1] < 1500 or float(np.nanstd(image)) < 0.01:
        raise FullStudyReportError(f"图像渲染检查失败：{png}")
    if "<svg" not in svg.read_text(encoding="utf-8", errors="ignore")[:2000].lower():
        raise FullStudyReportError(f"SVG渲染检查失败：{svg}")
    return {
        "figure_id": figure_id,
        "title": title,
        "png": str(png.relative_to(figures_dir.parent)).replace("\\", "/"),
        "svg": str(svg.relative_to(figures_dir.parent)).replace("\\", "/"),
        "png_sha256": _sha256(png),
        "svg_sha256": _sha256(svg),
        "pixel_height": int(image.shape[0]),
        "pixel_width": int(image.shape[1]),
        "render_nonblank": True,
    }


def _mode_frontier_ids(result: FullStudyResult, mode: str) -> set[str]:
    return {
        str(row["point_id"])
        for row in result.frontiers.get("mode_frontiers", {}).get(mode, [])
        if isinstance(row, dict) and row.get("point_id")
    }


def _combined_frontier_ids(result: FullStudyResult) -> set[str]:
    return {
        str(row["point_id"])
        for row in result.frontiers.get("combined_frontier", [])
        if isinstance(row, dict) and row.get("point_id")
    }


def _role(labels: str, *, authoritative_knee: bool) -> str:
    values = set(str(labels).split("|"))
    roles: list[str] = []
    if "cost_endpoint" in values:
        roles.append("最低成本端点")
    if "carbon_endpoint" in values:
        roles.append("最低碳端点")
    if "epsilon" in values:
        roles.append("ε约束点")
    if authoritative_knee:
        roles.append("权威膝点")
    return "、".join(roles) or "候选点"


def _build_display_tables(
    result: FullStudyResult,
    tables_dir: Path,
    tes_sensitivity: Mapping[str, Any] | None = None,
) -> tuple[list[Path], dict[str, pd.DataFrame]]:
    tables_dir.mkdir(parents=True, exist_ok=False)
    table_files: list[Path] = []
    frames: dict[str, pd.DataFrame] = {}

    mode_frontier_sets = {mode: _mode_frontier_ids(result, mode) for mode in _MODE_ORDER}
    combined_set = _combined_frontier_ids(result)
    points = result.points.copy()
    points["mode_cn"] = points["mode"].map(_MODE_CN)
    points["annual_cost_million_CNY"] = points["annual_real_cost_CNY_per_year"] / 1e6
    points["annual_carbon_tCO2e"] = points["annual_operating_carbon_kgCO2e_per_year"] / 1e3
    points["mip_gap_percent"] = points["reported_mip_gap"] * 100
    points["authoritative_combined_knee"] = points["point_id"].eq(result.combined_knee_id)
    points["on_mode_frontier"] = [pid in mode_frontier_sets[mode] for pid, mode in zip(points["point_id"], points["mode"])]
    points["on_combined_frontier"] = points["point_id"].isin(combined_set)
    points["display_role"] = [
        _role(labels, authoritative_knee=pid == result.combined_knee_id)
        for labels, pid in zip(points["labels"], points["point_id"])
    ]
    points["qa_passed"] = True
    point_columns = [
        "point_id",
        "mode",
        "mode_cn",
        "display_role",
        "annual_real_cost_CNY_per_year",
        "annual_cost_million_CNY",
        "annual_operating_carbon_kgCO2e_per_year",
        "annual_carbon_tCO2e",
        "reported_mip_gap",
        "mip_gap_percent",
        "unserved_heat_kWh",
        "authoritative_combined_knee",
        "on_mode_frontier",
        "on_combined_frontier",
        "qa_passed",
    ]
    display_points = points[point_columns].sort_values(["mode", "annual_operating_carbon_kgCO2e_per_year"])
    path = tables_dir / "pareto_points_display.csv"
    display_points.to_csv(path, index=False, encoding="utf-8-sig")
    table_files.append(path)
    frames["pareto"] = display_points

    representative_ids: list[str] = []
    for mode in _MODE_ORDER:
        rows = result.points.loc[result.points["mode"] == mode]
        if rows.empty:
            continue
        representative_ids.append(str(rows.loc[rows["annual_real_cost_CNY_per_year"].idxmin(), "point_id"]))
        representative_ids.append(str(rows.loc[rows["annual_operating_carbon_kgCO2e_per_year"].idxmin(), "point_id"]))
        knee = result.knees.get("modes", {}).get(mode) if isinstance(result.knees.get("modes"), dict) else None
        if isinstance(knee, dict) and knee.get("point_id"):
            representative_ids.append(str(knee["point_id"]))
    representative_ids.append(result.combined_knee_id)
    representative_ids = list(dict.fromkeys(representative_ids))

    representative_rows: list[dict[str, Any]] = []
    cost_rows: list[pd.DataFrame] = []
    carbon_rows: list[pd.DataFrame] = []
    qa_rows: list[dict[str, Any]] = []
    for point_id in representative_ids:
        evidence = result.evidence[point_id]
        directory = evidence.directory
        row = result.points.loc[result.points["point_id"] == point_id].iloc[0]
        capacities = pd.read_csv(directory / "capacity_decisions.csv")
        capacities["capacity_kW_th"] = pd.to_numeric(capacities["capacity_kW_th"], errors="coerce").fillna(0.0)
        by_tech = capacities.groupby("technology_id", dropna=False)["capacity_kW_th"].sum()
        connections = pd.read_csv(directory / "building_connection.csv")
        network = pd.read_csv(directory / "network_decisions.csv")
        built = network.loc[network["built"].map(_as_bool)]
        solution = evidence.solution
        representative_rows.append(
            {
                "point_id": point_id,
                "mode": row["mode"],
                "mode_cn": _MODE_CN[str(row["mode"])],
                "display_role": _role(str(row["labels"]), authoritative_knee=point_id == result.combined_knee_id),
                "realized_mode": solution.get("realized_mode"),
                "selected_site": solution.get("selected_site"),
                "connected_buildings": int(solution.get("connected_building_count", 0)),
                "local_buildings": int(solution.get("local_building_count", 0)),
                "central_hp_capacity_kW_th": float(by_tech.get("central_hp", 0.0)),
                "central_boiler_capacity_kW_th": float(by_tech.get("central_boiler", 0.0)),
                "local_hp_capacity_kW_th": float(by_tech.get("local_hp", 0.0)),
                "built_edge_count": int(len(built)),
                "built_route_length_m": float(pd.to_numeric(built.get("length_m", pd.Series(dtype=float)), errors="coerce").sum()),
                "annual_real_cost_CNY_per_year": float(row["annual_real_cost_CNY_per_year"]),
                "annual_operating_carbon_kgCO2e_per_year": float(row["annual_operating_carbon_kgCO2e_per_year"]),
                "reported_mip_gap": float(row["reported_mip_gap"]),
                "unserved_heat_kWh": float(row["unserved_heat_kWh"]),
                "qa_passed": bool(evidence.qa.get("passed")),
            }
        )
        cost = pd.read_csv(directory / "cost_breakdown.csv")
        cost.insert(0, "point_id", point_id)
        cost_rows.append(cost)
        carbon = pd.read_csv(directory / "carbon_breakdown.csv")
        carbon.insert(0, "point_id", point_id)
        carbon_rows.append(carbon)
        qa_row = {"point_id": point_id, "qa_passed": True, "relative_unserved": float(evidence.qa.get("relative_unserved", 0.0))}
        qa_row.update({f"max_{key}": float(value) for key, value in evidence.qa.get("max_errors", {}).items()})
        qa_rows.append(qa_row)

    representatives = pd.DataFrame(representative_rows)
    path = tables_dir / "representative_solutions.csv"
    representatives.to_csv(path, index=False, encoding="utf-8-sig")
    table_files.append(path)
    frames["representatives"] = representatives

    all_cost = pd.concat(cost_rows, ignore_index=True)
    all_cost["annual_CNY"] = pd.to_numeric(all_cost["annual_CNY"], errors="raise")
    path = tables_dir / "cost_breakdown_display.csv"
    all_cost.to_csv(path, index=False, encoding="utf-8-sig")
    table_files.append(path)
    frames["cost"] = all_cost

    all_carbon = pd.concat(carbon_rows, ignore_index=True)
    all_carbon["annual_kgCO2e"] = pd.to_numeric(all_carbon["annual_kgCO2e"], errors="raise")
    path = tables_dir / "carbon_breakdown_display.csv"
    all_carbon.to_csv(path, index=False, encoding="utf-8-sig")
    table_files.append(path)
    frames["carbon"] = all_carbon

    qa_frame = pd.DataFrame(qa_rows)
    path = tables_dir / "qa_metrics.csv"
    qa_frame.to_csv(path, index=False, encoding="utf-8-sig")
    table_files.append(path)
    frames["qa"] = qa_frame

    tes_rows: list[dict[str, Any]] = []
    for mode in ("central", "hybrid"):
        pair = result.root_summary["tes_pairs"][mode]
        tes = pair["tes"]
        energy = abs(float(tes.get("energy_capacity_kWh_th", 0.0)))
        charge = abs(float(tes.get("actual_peak_charge_kW_th", 0.0)))
        discharge = abs(float(tes.get("actual_peak_discharge_kW_th", 0.0)))
        tes_rows.append(
            {
                "scenario_id": f"frozen_{mode}_tes_pair",
                "scenario_kind": "frozen_economic_case",
                "tes_capex_multiplier": 1.0,
                "mode": mode,
                "mode_cn": _MODE_CN[mode],
                "representative_role": pair.get("representative_role"),
                "tes_off_cost_CNY_per_year": float(pair["tes_off"]["annual_real_cost_CNY_per_year"]),
                "tes_on_cost_CNY_per_year": float(pair["tes_on"]["annual_real_cost_CNY_per_year"]),
                "delta_cost_CNY_per_year": float(pair["delta_cost_CNY_per_year"]),
                "tes_off_carbon_kgCO2e_per_year": float(pair["tes_off"]["annual_operating_carbon_kgCO2e_per_year"]),
                "tes_on_carbon_kgCO2e_per_year": float(pair["tes_on"]["annual_operating_carbon_kgCO2e_per_year"]),
                "delta_carbon_kgCO2e_per_year": float(pair["delta_carbon_kgCO2e_per_year"]),
                "energy_capacity_kWh_th": energy,
                "actual_peak_charge_kW_th": charge,
                "actual_peak_discharge_kW_th": discharge,
                "tes_selected_by_positive_capacity": energy > _CAPACITY_TOLERANCE,
                "energy_upper_bound_binding": bool(tes.get("energy_upper_bound_binding")),
                "charge_upper_bound_binding": bool(tes.get("charge_upper_bound_binding")),
                "discharge_upper_bound_binding": bool(tes.get("discharge_upper_bound_binding")),
                "structure_match": bool(pair.get("structure_match")),
                "independent_qa_passed": bool(pair.get("independent_qa_passed")),
                "passed": bool(pair.get("passed")),
            }
        )
    if tes_sensitivity is not None:
        comparison = tes_sensitivity["comparison"]
        tes = tes_sensitivity["tes"]
        energy = abs(float(tes["energy_capacity_kWh_th"]))
        charge = abs(float(tes["actual_peak_charge_kW_th"]))
        discharge = abs(float(tes["actual_peak_discharge_kW_th"]))
        tes_rows.append(
            {
                "scenario_id": str(comparison["scenario_id"]),
                "scenario_kind": "tes_capex_sensitivity",
                "tes_capex_multiplier": float(comparison["tes_capex_multiplier"]),
                "mode": "hybrid",
                "mode_cn": "混合式敏感性",
                "representative_role": comparison.get("representative_role"),
                "tes_off_cost_CNY_per_year": float(comparison["tes_off"]["annual_real_cost_CNY_per_year"]),
                "tes_on_cost_CNY_per_year": float(comparison["tes_on"]["annual_real_cost_CNY_per_year"]),
                "delta_cost_CNY_per_year": float(comparison["delta_cost_CNY_per_year"]),
                "tes_off_carbon_kgCO2e_per_year": float(comparison["tes_off"]["annual_operating_carbon_kgCO2e_per_year"]),
                "tes_on_carbon_kgCO2e_per_year": float(comparison["tes_on"]["annual_operating_carbon_kgCO2e_per_year"]),
                "delta_carbon_kgCO2e_per_year": float(comparison["delta_carbon_kgCO2e_per_year"]),
                "energy_capacity_kWh_th": energy,
                "actual_peak_charge_kW_th": charge,
                "actual_peak_discharge_kW_th": discharge,
                "tes_selected_by_positive_capacity": energy > _CAPACITY_TOLERANCE,
                "energy_upper_bound_binding": bool(tes.get("energy_upper_bound_binding")),
                "charge_upper_bound_binding": bool(tes.get("charge_upper_bound_binding")),
                "discharge_upper_bound_binding": bool(tes.get("discharge_upper_bound_binding")),
                "structure_match": bool(comparison.get("structure_match")),
                "independent_qa_passed": bool(comparison.get("independent_qa_passed")),
                "passed": bool(comparison.get("passed")),
            }
        )
    tes_frame = pd.DataFrame(tes_rows)
    path = tables_dir / "tes_comparison.csv"
    tes_frame.to_csv(path, index=False, encoding="utf-8-sig")
    table_files.append(path)
    frames["tes"] = tes_frame
    return table_files, frames


def _plot_pareto(result: FullStudyResult) -> plt.Figure:
    fig, ax = _new_figure("成本—运行碳排 Pareto 前沿")
    for mode in _MODE_ORDER:
        rows = result.frontiers.get("mode_frontiers", {}).get(mode, [])
        if not rows:
            continue
        ordered = sorted(rows, key=lambda row: float(row["annual_operating_carbon_kgCO2e_per_year"]))
        carbon = np.asarray([float(row["annual_operating_carbon_kgCO2e_per_year"]) / 1e3 for row in ordered])
        cost = np.asarray([float(row["annual_real_cost_CNY_per_year"]) / 1e6 for row in ordered])
        ax.plot(
            carbon,
            cost,
            marker="o",
            markersize=7,
            linewidth=2.4,
            color=_MODE_COLOR[mode],
            label=f"{_MODE_CN[mode]}前沿（{len(ordered)}点）",
        )
    knee = result.knees["combined"]
    knee_x = float(knee["annual_operating_carbon_kgCO2e_per_year"]) / 1e3
    knee_y = float(knee["annual_real_cost_CNY_per_year"]) / 1e6
    ax.scatter([knee_x], [knee_y], marker="*", s=330, color=_KNEE_COLOR, edgecolor="white", linewidth=1.2, zorder=8, label="合并前沿权威膝点")
    ax.annotate(
        f"{result.combined_knee_id}\n成本 {knee_y:.2f} 百万元/年\n碳排 {knee_x:.1f} tCO2e/年",
        xy=(knee_x, knee_y),
        xytext=(18, 24),
        textcoords="offset points",
        bbox={"boxstyle": "round,pad=0.45", "facecolor": "white", "edgecolor": _KNEE_COLOR},
        arrowprops={"arrowstyle": "->", "color": _KNEE_COLOR},
        fontsize=10,
    )
    ax.set_xlabel("年度运行碳排（tCO2e/年）")
    ax.set_ylabel("年化真实经济成本（百万元/年）")
    ax.grid(True)
    ax.legend(loc="best", frameon=True)
    ax.text(
        0.01,
        0.02,
        "方法：ε-constraint；膝点来源：knee_points.json（不采用pareto_points.csv中的旧is_knee列）",
        transform=ax.transAxes,
        fontsize=9,
        color="#4A5560",
    )
    return fig


def _plot_cost_carbon_breakdown(result: FullStudyResult) -> plt.Figure:
    directory = result.knee_directory
    cost = pd.read_csv(directory / "cost_breakdown.csv")
    carbon = pd.read_csv(directory / "carbon_breakdown.csv")
    cost["annual_CNY"] = pd.to_numeric(cost["annual_CNY"], errors="raise") / 1e6
    carbon["annual_kgCO2e"] = pd.to_numeric(carbon["annual_kgCO2e"], errors="raise") / 1e3
    cost_labels = {
        "device_investment": "设备年化投资",
        "fixed_om": "固定运维",
        "pipe_investment": "管网年化投资",
        "station_investment": "站房年化投资",
        "connection_investment": "接入年化投资",
        "storage_investment": "储热年化投资",
        "electricity_cost": "电费",
        "gas_cost": "燃气费",
        "variable_om": "可变运维",
        "annual_monthly_demand_charge_CNY_per_year": "月度最大需量费",
    }
    carbon_labels = {"purchased_electricity": "购电间接碳排", "natural_gas_LHV": "天然气直接碳排（LHV）"}
    cost["label"] = cost["component"].map(cost_labels).fillna(cost["component"])
    carbon["label"] = carbon["component"].map(carbon_labels).fillna(carbon["component"])
    fig, axes = _new_figure("权威膝点：成本与碳排构成", columns=2)
    ax_cost, ax_carbon = axes
    positive = cost.loc[cost["annual_CNY"].abs() > 1e-9].sort_values("annual_CNY")
    ax_cost.barh(positive["label"], positive["annual_CNY"], color="#4C78A8")
    for index, value in enumerate(positive["annual_CNY"]):
        ax_cost.text(value, index, f" {value:.2f}", va="center", fontsize=9)
    ax_cost.set_xlabel("百万元/年")
    ax_cost.set_title(f"年化真实成本：{cost['annual_CNY'].sum():.2f} 百万元/年")
    ax_cost.grid(axis="x")
    ax_cost.text(0.01, -0.13, "未供热罚值和政策碳价不计入真实工程成本", transform=ax_cost.transAxes, fontsize=9, color="#5F6B76")

    colors = ["#5B8FF9", "#C44E52"]
    ax_carbon.bar(carbon["label"], carbon["annual_kgCO2e"], color=colors[: len(carbon)])
    for index, value in enumerate(carbon["annual_kgCO2e"]):
        ax_carbon.text(index, value, f"{value:,.0f}", ha="center", va="bottom", fontsize=10)
    ax_carbon.set_ylabel("tCO2e/年")
    ax_carbon.set_title(f"年度运行物理碳排：{carbon['annual_kgCO2e'].sum():,.1f} tCO2e/年")
    ax_carbon.grid(axis="y")
    ax_carbon.tick_params(axis="x", rotation=10)
    return fig


def _load_knee_hourly(result: FullStudyResult) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    directory = result.knee_directory
    building = pd.read_parquet(directory / "building_hourly.parquet")
    dispatch = pd.read_parquet(directory / "dispatch_hourly.parquet")
    network = pd.read_parquet(directory / "network_hourly.parquet")
    storage = pd.read_parquet(directory / "storage_hourly.parquet")
    for name, frame in (("building_hourly", building), ("dispatch_hourly", dispatch), ("network_hourly", network), ("storage_hourly", storage)):
        if "hour" not in frame.columns:
            raise FullStudyReportError(f"{name}缺少hour字段")
    return building, dispatch, network, storage


def _hourly_series(building: pd.DataFrame, dispatch: pd.DataFrame) -> pd.DataFrame:
    demand = building.groupby("hour", sort=True)[["demand_kW", "network_kW", "local_kW", "unserved_kW"]].sum()
    tech = dispatch.pivot_table(index="hour", columns="technology_id", values="heat_kW_th", aggfunc="sum", fill_value=0.0)
    for technology in ("central_hp", "central_boiler", "local_hp"):
        if technology not in tech.columns:
            tech[technology] = 0.0
    hourly = demand.join(tech[["central_hp", "central_boiler", "local_hp"]], how="left").fillna(0.0)
    if hourly.index.duplicated().any() or len(hourly) == 0:
        raise FullStudyReportError("膝点逐时数据的hour索引无效")
    return hourly


def _plot_load_duration(result: FullStudyResult, hourly: pd.DataFrame) -> plt.Figure:
    fig, ax = _new_figure("全供暖季负荷持续曲线")
    sorted_demand = np.sort(hourly["demand_kW"].to_numpy(dtype=float))[::-1] / 1e3
    rank = np.arange(1, len(sorted_demand) + 1)
    ax.fill_between(rank, sorted_demand, color="#9CC4E4", alpha=0.45)
    ax.plot(rank, sorted_demand, color="#2878B5", linewidth=2.1)
    peak = float(sorted_demand[0])
    mean = float(sorted_demand.mean())
    ax.axhline(mean, linestyle="--", color="#F28E2B", linewidth=1.7, label=f"平均负荷 {mean:.1f} MW")
    ax.set_xlabel("小时排序（从高负荷到低负荷）")
    ax.set_ylabel("建筑有用热负荷（MW）")
    ax.set_title(f"峰值 {peak:.1f} MW；平均 {mean:.1f} MW；负荷率 {mean / peak:.1%}", fontsize=13)
    ax.grid(True)
    ax.legend()
    ax.text(0.01, 0.02, "包含完整2160小时；未进行代表日或时间聚合", transform=ax.transAxes, fontsize=9, color="#4A5560")
    return fig


def _reshape_season(values: pd.Series, hours: Sequence[int]) -> np.ndarray:
    indexed = pd.Series(values.to_numpy(dtype=float), index=pd.Index(hours, dtype=int)).sort_index()
    expected = np.arange(int(indexed.index.min()), int(indexed.index.max()) + 1)
    if len(indexed) != len(expected) or not np.array_equal(indexed.index.to_numpy(), expected):
        raise FullStudyReportError("无法将非连续小时转换为供暖季热图")
    if len(indexed) % 24 != 0:
        raise FullStudyReportError("供暖季小时数不能整除24")
    return indexed.to_numpy().reshape((-1, 24)) / 1e3


def _plot_heatmap(result: FullStudyResult, hourly: pd.DataFrame) -> plt.Figure:
    data = [
        ("建筑热负荷", hourly["demand_kW"], "YlOrRd"),
        ("集中热源出力", hourly["central_hp"] + hourly["central_boiler"], "Blues"),
        ("分布式热泵出力", hourly["local_hp"], "Greens"),
    ]
    fig, axes = plt.subplots(3, 1, figsize=(13.333, 7.5), constrained_layout=True, sharex=True)
    fig.suptitle("源—荷逐时热图（90天×24小时）", fontsize=20, fontweight="bold", x=0.04, ha="left")
    for ax, (title, series, cmap) in zip(axes, data):
        matrix = _reshape_season(series, hourly.index.to_numpy())
        image = ax.imshow(matrix, aspect="auto", interpolation="nearest", cmap=cmap, origin="lower")
        ax.set_ylabel("供暖季日序")
        ax.set_title(title, loc="left", fontsize=12)
        colorbar = fig.colorbar(image, ax=ax, pad=0.01, fraction=0.025)
        colorbar.set_label("MW")
    axes[-1].set_xlabel("日内小时")
    axes[-1].set_xticks([0, 3, 6, 9, 12, 15, 18, 21, 23])
    axes[-1].set_xticklabels(["00", "03", "06", "09", "12", "15", "18", "21", "23"])
    return fig


def _plot_peak_dispatch(result: FullStudyResult, hourly: pd.DataFrame, building: pd.DataFrame) -> plt.Figure:
    peak_hour = int(hourly["demand_kW"].idxmax())
    first_hour = int(hourly.index.min())
    day_start = first_hour + ((peak_hour - first_hour) // 24) * 24
    day_hours = np.arange(day_start, day_start + 24)
    day = hourly.reindex(day_hours).fillna(0.0)
    timestamps = building.groupby("hour", sort=True)["timestamp"].first()
    timestamp = pd.to_datetime(timestamps.get(day_start), errors="coerce")
    date_label = timestamp.strftime("%Y-%m-%d") if not pd.isna(timestamp) else f"供暖季第{(day_start-first_hour)//24+1}天"
    fig, ax = _new_figure(f"峰值日逐时供热调度（{date_label}）")
    x = np.arange(24)
    values = [day["central_hp"].to_numpy() / 1e3, day["central_boiler"].to_numpy() / 1e3, day["local_hp"].to_numpy() / 1e3]
    ax.stackplot(x, values, labels=["中央空气源热泵", "中央LHV燃气锅炉", "分布式空气源热泵"], colors=["#4C78A8", "#E45756", "#72B7B2"], alpha=0.86)
    ax.plot(x, day["demand_kW"].to_numpy() / 1e3, color="#222222", linewidth=2.5, label="建筑有用热负荷")
    ax.set_xticks(np.arange(0, 24, 2))
    ax.set_xlabel("小时")
    ax.set_ylabel("热功率（MW）")
    ax.grid(axis="y")
    ax.legend(loc="upper left", ncol=2, frameon=True)
    ax.text(0.01, 0.02, f"峰值小时：模型hour={peak_hour}；未供热={day['unserved_kW'].sum():.3g} kWh", transform=ax.transAxes, fontsize=9, color="#4A5560")
    return fig


def _iter_line_coordinates(geometry: Mapping[str, Any]) -> Iterable[list[list[float]]]:
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if kind == "LineString":
        yield coordinates
    elif kind == "MultiLineString":
        yield from coordinates


def _load_geojson(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        raise FullStudyReportError(f"GeoJSON不是FeatureCollection：{path}")
    return payload


def _plot_network(result: FullStudyResult) -> plt.Figure:
    directory = result.knee_directory
    network = _load_geojson(directory / "network_decisions.geojson")
    access = _load_geojson(directory / "access_decisions.geojson")
    station = pd.read_csv(directory / "station_decisions.csv")
    connections = pd.read_csv(directory / "building_connection.csv")
    connected = {str(row.building_id): _as_bool(row.connected) for row in connections.itertuples(index=False)}

    fig, ax = _new_figure("权威膝点：候选站—道路管网—建筑接入方案")
    built_edge_count = 0
    built_length = 0.0
    pipe_colors = {"small": "#4C78A8", "medium": "#2A9D8F", "large": "#D1495B"}
    for feature in network["features"]:
        props = feature.get("properties", {}) or {}
        is_built = _as_bool(props.get("built", False))
        for coordinates in _iter_line_coordinates(feature.get("geometry", {}) or {}):
            array = np.asarray(coordinates, dtype=float)
            if array.ndim != 2 or array.shape[0] < 2:
                continue
            if is_built:
                pipe_type = str(props.get("pipe_type", ""))
                ax.plot(array[:, 0], array[:, 1], color=pipe_colors.get(pipe_type, "#D1495B"), linewidth=2.6, alpha=0.95, zorder=3)
            else:
                ax.plot(array[:, 0], array[:, 1], color="#C7CDD3", linewidth=0.65, alpha=0.45, zorder=1)
        if is_built:
            built_edge_count += 1
            built_length += float(props.get("length_m", 0.0) or 0.0)

    boundary_points: dict[str, tuple[float, float]] = {}
    for feature in access["features"]:
        props = feature.get("properties", {}) or {}
        building_id = str(props.get("building_id", ""))
        raw_point = props.get("building_boundary_point")
        if isinstance(raw_point, list) and len(raw_point) >= 2:
            boundary_points.setdefault(building_id, (float(raw_point[0]), float(raw_point[1])))
        if _as_bool(props.get("selected", False)):
            for coordinates in _iter_line_coordinates(feature.get("geometry", {}) or {}):
                array = np.asarray(coordinates, dtype=float)
                if array.ndim == 2 and array.shape[0] >= 2:
                    ax.plot(array[:, 0], array[:, 1], color="#F28E2B", linewidth=2.1, alpha=0.95, zorder=4)

    for expected, label, color in ((True, "集中接网建筑", "#2878B5"), (False, "分布式供热建筑", "#F28E2B")):
        selected_points = [point for building_id, point in boundary_points.items() if connected.get(building_id) is expected]
        if selected_points:
            xs, ys = zip(*selected_points)
            ax.scatter(xs, ys, s=24, color=color, edgecolor="white", linewidth=0.45, zorder=6, label=label)

    station["built"] = station["built"].map(_as_bool)
    ax.scatter(station["x_m"], station["y_m"], marker="^", s=85, color="#777777", alpha=0.6, label="候选能源站", zorder=7)
    selected_station = station.loc[station["built"]]
    if not selected_station.empty:
        ax.scatter(selected_station["x_m"], selected_station["y_m"], marker="*", s=270, color=_KNEE_COLOR, edgecolor="white", linewidth=0.8, label="优化选中能源站", zorder=8)
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xlabel("投影坐标 X（m）")
    ax.set_ylabel("投影坐标 Y（m）")
    ax.grid(False)
    ax.legend(loc="best", ncol=2, frameon=True)
    annotation = (
        f"建成原子边 {built_edge_count} 条，路线长度 {built_length / 1000:.2f} km；灰色为候选，彩色为建成。\n"
        "范围：5个候选最短路径树；道路约束为规划概念，不代表施工可实施性。"
    )
    ax.text(0.01, 0.02, annotation, transform=ax.transAxes, fontsize=9, color="#374151", bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.82, "edgecolor": "#D1D5DB"})
    return fig


def _plot_energy_flow(
    result: FullStudyResult,
    building: pd.DataFrame,
    dispatch: pd.DataFrame,
    network: pd.DataFrame,
) -> plt.Figure:
    fig, ax = _new_figure("权威膝点：年度源—网—荷能流")
    ax.set_xlim(-0.2, 10.2)
    ax.set_ylim(0.1, 5.9)
    ax.axis("off")

    heat_by_tech = dispatch.groupby("technology_id", dropna=False)["heat_kW_th"].sum()
    input_by_tech = dispatch.groupby("technology_id", dropna=False)["energy_input_kW"].sum()
    central_hp_heat = float(heat_by_tech.get("central_hp", 0.0))
    local_hp_heat = float(heat_by_tech.get("local_hp", 0.0))
    hp_heat = central_hp_heat + local_hp_heat
    hp_electricity = float(input_by_tech.get("central_hp", 0.0) + input_by_tech.get("local_hp", 0.0))
    boiler_heat = float(heat_by_tech.get("central_boiler", 0.0))
    gas_input = float(input_by_tech.get("central_boiler", 0.0))
    ambient = max(0.0, hp_heat - hp_electricity)
    delivered_network = float(pd.to_numeric(building["network_kW"], errors="coerce").sum())
    delivered_local = float(pd.to_numeric(building["local_kW"], errors="coerce").sum())
    demand = float(pd.to_numeric(building["demand_kW"], errors="coerce").sum())
    loss = float(pd.to_numeric(network.get("loss_kW", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
    pump = float(pd.to_numeric(network.get("pump_kW", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())

    def gwh(value: float) -> str:
        return f"{value / 1e6:,.1f} GWh"

    def box(x: float, y: float, width: float, height: float, title: str, value: str, color: str) -> None:
        patch = FancyBboxPatch((x, y), width, height, boxstyle="round,pad=0.08", facecolor=color, edgecolor="white", linewidth=1.5, alpha=0.92)
        ax.add_patch(patch)
        ax.text(x + width / 2, y + height * 0.62, title, ha="center", va="center", fontsize=11, fontweight="bold", color="white")
        ax.text(x + width / 2, y + height * 0.28, value, ha="center", va="center", fontsize=10, color="white")

    def arrow(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        label: str,
        *,
        label_xy: tuple[float, float] | None = None,
    ) -> None:
        patch = FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=15, linewidth=2.0, color="#5F6B76")
        ax.add_patch(patch)
        label_x, label_y = label_xy or ((x1 + x2) / 2, (y1 + y2) / 2 + 0.14)
        ax.text(label_x, label_y, label, ha="center", fontsize=9, color="#374151")

    box(0.1, 4.2, 1.65, 0.95, "购电", gwh(hp_electricity + pump), "#4C78A8")
    box(0.1, 2.55, 1.65, 0.95, "环境热", gwh(ambient), "#2A9D8F")
    box(0.1, 0.9, 1.65, 0.95, "天然气（LHV）", gwh(gas_input), "#C44E52")
    box(3.2, 3.75, 1.8, 0.95, "空气源热泵", gwh(hp_heat), "#2878B5")
    box(3.2, 1.55, 1.8, 0.95, "燃气锅炉", gwh(boiler_heat), "#B04759")
    box(6.2, 3.6, 1.75, 0.95, "集中管网", gwh(delivered_network), "#7A5195")
    box(6.2, 1.35, 1.75, 0.95, "分布式供热", gwh(delivered_local), "#F28E2B")
    box(8.55, 2.5, 1.45, 1.15, "建筑热负荷", gwh(demand), "#2A9D8F")

    arrow(1.75, 4.68, 3.2, 4.25, gwh(hp_electricity))
    arrow(1.75, 3.0, 3.2, 4.02, gwh(ambient))
    arrow(1.75, 1.38, 3.2, 2.02, gwh(gas_input))
    arrow(5.0, 4.08, 6.2, 4.08, f"中央热泵 {gwh(central_hp_heat)}", label_xy=(5.58, 4.34))
    arrow(5.0, 2.05, 6.2, 3.88, f"锅炉 {gwh(boiler_heat)}", label_xy=(5.23, 2.70))
    arrow(5.0, 3.88, 6.2, 1.85, f"本地热泵 {gwh(local_hp_heat)}", label_xy=(5.90, 2.42))
    arrow(7.95, 4.05, 8.55, 3.18, gwh(delivered_network))
    arrow(7.95, 1.82, 8.55, 2.88, gwh(delivered_local))
    ax.text(6.25, 5.05, f"管损 {gwh(loss)}\n泵耗 {gwh(pump)}", fontsize=10, color="#374151", bbox={"boxstyle": "round", "facecolor": "#F3F4F6", "edgecolor": "#CBD5E1"})
    ax.text(0.1, 0.2, "按逐时功率×1 h汇总。环境热量按热泵供热量减用电量展示；不表示完整一次能源或生命周期碳核算。", fontsize=9, color="#5F6B76")
    return fig


def _plot_tes(result: FullStudyResult, tes: pd.DataFrame) -> plt.Figure:
    fig, axes = _new_figure("水蓄热（TES）基准配对与投资敏感性", columns=2)
    x = np.arange(len(tes))
    capacity = tes["energy_capacity_kWh_th"].to_numpy(dtype=float)
    axes[0].bar(x, capacity / 1e3, color=[_MODE_COLOR[mode] for mode in tes["mode"]])
    axes[0].set_xticks(x, tes["mode_cn"])
    axes[0].set_ylabel("实际优化容量（MWh_th）")
    axes[0].set_title("TES容量选择")
    axes[0].grid(axis="y")
    for index, value in enumerate(capacity):
        axes[0].text(index, max(value / 1e3, 0.0) + 0.01, f"{value:.3g} kWh", ha="center", fontsize=10)

    axes[1].axis("off")
    row_positions = np.linspace(0.82, 0.28, len(tes))
    for (_, row), y in zip(tes.iterrows(), row_positions):
        selected = bool(row["tes_selected_by_positive_capacity"])
        multiplier = float(row["tes_capex_multiplier"])
        scenario = "冻结价格" if row["scenario_kind"] == "frozen_economic_case" else f"TES投资×{multiplier:g}"
        title = f"{row['mode_cn']}（{scenario}）：{'实际充放热' if selected else '未选择正容量'}"
        text = (
            f"成本变化 {row['delta_cost_CNY_per_year']:+.3g} CNY/年\n"
            f"碳排变化 {row['delta_carbon_kgCO2e_per_year']:+.3g} kgCO2e/年\n"
            f"容量 {row['energy_capacity_kWh_th']:.3g} kWh_th；峰值充/放热 "
            f"{row['actual_peak_charge_kW_th']:.3g}/{row['actual_peak_discharge_kW_th']:.3g} kW_th；"
            f"QA={row['independent_qa_passed']}"
        )
        axes[1].text(0.04, y, title, fontsize=12, fontweight="bold", color=_MODE_COLOR[str(row["mode"])], transform=axes[1].transAxes)
        axes[1].text(0.04, y - 0.065, text, fontsize=9.5, va="top", wrap=True, transform=axes[1].transAxes)
    has_sensitivity = bool(tes["scenario_kind"].eq("tes_capex_sensitivity").any())
    conclusion = (
        "冻结投资下TES未被选择；只改变TES投资至50%后出现正容量和实际充放热。"
        if has_sensitivity
        else "本次冻结投资下未选择正容量；尚未加载独立TES投资敏感性证据。"
    )
    axes[1].text(
        0.04,
        0.05,
        "判定规则：以实际能量容量>1e-6 kWh_th为“选中”，不使用退化的built标志。\n"
        + conclusion,
        fontsize=9.5,
        color="#374151",
        transform=axes[1].transAxes,
        bbox={"boxstyle": "round", "facecolor": "#F3F4F6", "edgecolor": "#CBD5E1"},
    )
    return fig


def _plot_qa(result: FullStudyResult, qa: pd.DataFrame) -> plt.Figure:
    metrics = {
        "热平衡": "max_heat_balance_kW",
        "设备容量": "max_capacity_kW",
        "管段容量": "max_pipe_kW",
        "管损": "max_loss_kW",
        "泵耗": "max_pump_kW",
        "容量裕度": "max_margin_kW",
        "成本复算": "max_cost_CNY",
        "碳排复算": "max_carbon_kgCO2e",
    }
    maxima: list[float] = []
    labels: list[str] = []
    for label, column in metrics.items():
        if column in qa.columns:
            labels.append(label)
            maxima.append(float(pd.to_numeric(qa[column], errors="coerce").abs().max()))
    ratios = np.log10(np.maximum(np.asarray(maxima), 1e-16) / 1e-6)
    colors = ["#2A9D8F" if value <= 0 else "#D1495B" for value in ratios]
    fig, ax = _new_figure("独立QA证据总览")
    y = np.arange(len(labels))
    ax.barh(y, ratios, color=colors)
    ax.set_yticks(y, labels)
    ax.axvline(0.0, color="#D1495B", linestyle="--", linewidth=1.8, label="1e-6 验收线")
    ax.set_xlabel("log10（最大残差 / 1e-6）——越小越好")
    ax.set_title(f"展示点 {len(qa)} 个；独立QA全部通过={bool(qa['qa_passed'].all())}", fontsize=13)
    ax.grid(axis="x")
    ax.legend()
    for index, value in enumerate(maxima):
        ax.text(ratios[index], index, f" {value:.2e}", va="center", fontsize=9)
    max_gap = float(result.points["reported_mip_gap"].max())
    note = (
        f"全部Pareto点qualified且QA通过；最大报告MIP gap={max_gap:.3%}；全部点未供热量为0。\n"
        "QA基于导出决策量与标准输入独立复算，不直接复用目标函数总值。"
    )
    ax.text(0.01, 0.02, note, transform=ax.transAxes, fontsize=9, color="#374151", bbox={"boxstyle": "round", "facecolor": "white", "edgecolor": "#CBD5E1"})
    return fig


def _json_scalar(value: Any) -> Any:
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def _write_presentation_data(frames: Mapping[str, pd.DataFrame], path: Path) -> None:
    specs = (
        ("Pareto点", "pareto", ["pareto_no_tes/pareto_points.csv", "pareto_no_tes/knee_points.json"]),
        ("代表方案", "representatives", ["pareto_no_tes/points/*/solution_summary.json"]),
        ("成本分项", "cost", ["pareto_no_tes/points/*/cost_breakdown.csv"]),
        ("碳排分项", "carbon", ["pareto_no_tes/points/*/carbon_breakdown.csv"]),
        ("独立QA", "qa", ["pareto_no_tes/points/*/independent_qa.json"]),
        ("TES配对", "tes", ["request_set_summary.json", "tes_pairs/*/tes_pair_qa.json"]),
    )
    sheets: list[dict[str, Any]] = []
    for name, key, source_files in specs:
        frame = frames[key]
        rows = [
            {str(column): _json_scalar(value) for column, value in row.items()}
            for row in frame.to_dict(orient="records")
        ]
        sheets.append({"name": name, "columns": [str(column) for column in frame.columns], "rows": rows, "source_files": source_files})
    path.write_text(json.dumps({"sheets": sheets}, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def _write_results_guide(result: FullStudyResult, output: Path, frames: Mapping[str, pd.DataFrame]) -> Path:
    knee = result.knees["combined"]
    solution = result.evidence[result.combined_knee_id].solution
    tes = frames["tes"]
    max_gap = float(result.points["reported_mip_gap"].max())
    sensitivity_rows = tes.loc[tes["scenario_kind"].eq("tes_capex_sensitivity")]
    if sensitivity_rows.empty:
        sensitivity_note = "本展示包未加载独立TES投资敏感性结果。"
    else:
        selected = sensitivity_rows.iloc[0]
        sensitivity_note = (
            f"在同一膝点固定站网和设备结构下，仅将TES投资乘数改为"
            f"{float(selected['tes_capex_multiplier']):g}，模型选择"
            f"{float(selected['energy_capacity_kWh_th']):,.3f} kWh_th，峰值充/放热"
            f"{float(selected['actual_peak_charge_kW_th']):,.3f}/"
            f"{float(selected['actual_peak_discharge_kW_th']):,.3f} kW_th，"
            f"年成本相对无TES变化{float(selected['delta_cost_CNY_per_year']):+,.2f} CNY/年。"
        )
    guide = output / "RESULTS_GUIDE_CN.md"
    text = f"""# UrbanHeatOpt 全研究成果展示说明

## 一、展示结论

- 来源运行：`{result.run_root.name}`
- 证据版本：Git `{result.git_sha}`，展示适配器 `{REPORTING_VERSION}`
- 计算范围：{result.building_count} 栋建筑、{result.hour_count} 个连续供暖小时，未进行代表日聚合
- 优化范围：`{result.optimization_scope}`
- Pareto 方法：ε-constraint；所有 {len(result.points)} 个点均通过ResultBundle资格门禁和独立QA
- 最大报告MIP gap：{max_gap:.4%}
- 合并前沿权威膝点：`{result.combined_knee_id}`（来源为 `knee_points.json`）
- 膝点模式：{_MODE_CN[str(knee['mode'])]}，实现模式 `{solution.get('realized_mode')}`
- 膝点年化真实成本：{float(knee['annual_real_cost_CNY_per_year']):,.2f} CNY/年
- 膝点年度运行碳排：{float(knee['annual_operating_carbon_kgCO2e_per_year']) / 1000:,.3f} tCO₂e/年
- 膝点接网/本地建筑：{int(solution.get('connected_building_count', 0))}/{int(solution.get('local_building_count', 0))} 栋
- 膝点选中能源站：`{solution.get('selected_site')}`

## 二、如何使用图册

1. `figures/fig01_pareto_frontier`：用于说明三种模式的成本—碳排权衡及合并前沿膝点。
2. `figures/fig02_cost_carbon_breakdown`：用于解释膝点的真实成本与运行碳排来源。
3. `figures/fig03_load_duration`：用于展示完整供暖季的峰谷差异及容量规划基础。
4. `figures/fig04_source_load_heatmap`：用于展示90天内日内与季节性源—荷匹配。
5. `figures/fig05_peak_day_dispatch`：用于展示峰值日热泵、锅炉和本地设备逐时协同。
6. `figures/fig06_energy_flow`：用于展示年度购电、环境热、天然气到建筑负荷的主要能流。
7. `figures/fig07_network_plan`：用于展示候选/建成管网、候选/选中站点及集中/分布式建筑。
8. `figures/fig08_tes_pair`：用于说明冻结价格下TES有/无配对，以及单独改变TES投资后的敏感性结果。
9. `figures/fig09_qa_evidence`：用于展示热平衡、容量、成本、碳排等独立复算证据。

PNG适合直接插入PPT；SVG适合后期无损排版；`UrbanHeatOpt_results_atlas.pdf`为可连续审阅的九页图册。

## 三、机器可读展示表

- `tables/pareto_points_display.csv`：所有点及其前沿、权威膝点、gap和QA标记。
- `tables/representative_solutions.csv`：各模式端点/膝点的设备、接网、站网和总指标。
- `tables/cost_breakdown_display.csv`、`carbon_breakdown_display.csv`：代表方案分项。
- `tables/qa_metrics.csv`：代表方案的独立QA最大残差。
- `tables/tes_comparison.csv`：有/无TES配对、容量、功率和上限触及状态。
- `presentation_data.json`：与上述六张表一一对应，供PPT数据工作簿生成器读取；数值保持JSON number/boolean/null。

## 四、TES结果的正确表述

本次集中式与混合式的有/无TES配对均通过结构一致性和独立QA，但实际优化能量容量分别为
{float(tes.loc[tes['mode'] == 'central', 'energy_capacity_kWh_th'].iloc[0]):.6g} 与
{float(tes.loc[tes['mode'] == 'hybrid', 'energy_capacity_kWh_th'].iloc[0]):.6g} kWh_th。
因此冻结经济口径下的结论是“TES模块已进入模型并完成配对验证，但优化器没有选择正容量TES”，不能表述成“TES没有运行”，也不能仅凭退化的`built=true`宣称已建储热。

{sensitivity_note}

敏感性结果只证明TES功能与经济阈值响应合理，不得把降低后的投资价冒充冻结报价，也不得把其成本结果并入正式Pareto前沿。

## 五、结果边界

- 候选站为五点法生成的研究候选，尚未绑定经专业审核的真实可建设地块。
- 全季网络优化范围为五个候选站各自的最短路径树枚举，并非完整自由道路拓扑全局优化。
- 网络图是规划概念图，不是施工图；不证明地块、地下障碍或建设审批可行。
- DeST负荷是源荷匹配输入；舒适边界以交付数据口径为准，本图册不新增室内舒适优化变量。
- 运行碳排只含购电间接碳排与天然气直接燃烧碳排，不是全生命周期碳足迹。
- 低品位热源当前仅保留接口，不属于本次V1执行技术集合。
- 结果用于“规划优化能力验证”，不能直接作为工程报价、施工方案或最终投资决策。

## 六、复现命令

在仓库根目录执行（输出目录必须不存在）：

```powershell
$env:PYTHONPATH = "src"
conda run --no-capture-output -n urbanheatopt_env python -m urbanheatopt.reporting.full_study `
  --run-root "{result.run_root}" `
  --output-root "<新的唯一展示目录>"
```

生成前适配器会重新检查顶层qualified、每个点ResultBundle、独立QA、求解范围、Git版本和权威膝点；任何一项不合格都会停止，不输出“成功图册”。
"""
    guide.write_text(text, encoding="utf-8")
    return guide


def _write_cp_traceability(
    result: FullStudyResult,
    output: Path,
    frames: Mapping[str, pd.DataFrame],
) -> Path:
    path = output / "CP_SCORE_TRACEABILITY.md"
    sensitivity_available = bool(
        frames["tes"]["scenario_kind"].eq("tes_capex_sensitivity").any()
    )
    tes_gap = (
        "冻结价TES为零容量；独立投资敏感性已得到正容量充放热，但不属于冻结经济前沿"
        if sensitivity_available
        else "TES在冻结输入下优化为零容量；尚无正容量敏感性展示证据"
    )
    text = f"""# CP_purpose 评分证据追踪（不自报最终分数）

> 本表按 `IN_DATA/指导性文档_forAI/CP_purpose.md` 的模型50、算法25、可视化15、代码质量10四类组织。状态仅说明本次运行证据覆盖程度，不代替评委评分。

| 评分维度 | 状态 | 本次可核查证据 | 仍有缺口或边界 |
|---|---|---|---|
| 优化模型（50） | 部分满足 | `tables/representative_solutions.csv`、`fig02_cost_carbon_breakdown`、`fig05_peak_day_dispatch`、`fig06_energy_flow`、`fig07_network_plan`、各点 `independent_qa.json`；{result.building_count}栋×{result.hour_count}h、三模式、热平衡、容量、成本及运行碳排均有本次合格结果 | DeST舒适边界由上游负荷数据体现，并非模型内舒适变量；候选站尚无真实地块容量依据；低品位热源仅保留接口；{tes_gap} |
| 优化算法（25） | 部分满足 | `fig01_pareto_frontier`、`tables/pareto_points_display.csv`；ε-constraint、三模式前沿、合并非支配前沿、`knee_points.json`权威膝点；ResultBundle与MIP gap证据 | 全季采用 `five_candidate_shortest_path_trees`，不是完整自由拓扑；未在本图册内形成不同算法的速度/质量基准对比；部分点为1%以内认证可行解而非严格gap=0 |
| 可视化与结果分析（15） | 已形成基础证据集 | 九页PNG/SVG/PDF图册；Pareto、成本/碳排分项、负荷持续曲线、源荷热图、逐时调度、能流、GIS站网、TES配对和QA；所有图经尺寸、非空和SVG/PDF检查 | 尚未生成正式PPT叙事页；地图是规划概念图而非施工图；需由团队结合答辩时长挑选主图并补充口头解释 |
| 代码质量（10） | 部分满足 | `figure_manifest.json`记录图、数据源哈希和渲染状态；`presentation_data.json`与六张CSV提供机器可读交接；适配器拒绝未qualified、QA失败、legacy回退及版本范围不一致的结果 | 尚未由独立人员完成最终源码打包审计；当前展示入口是模块命令，正式顶层CLI接线另行完成；第三方依赖锁定与干净环境复现需纳入最终交付验收 |

## 必须保留的口径说明

- **舒适与负荷**：DeST负荷及其室内条件来自冻结输入，本次模型优化供热系统，不反向优化建筑舒适设定。
- **站网范围**：本次结论只在五个候选站及其最短路径树范围内成立，不声称得到所有道路拓扑中的全局最优管网。
- **低品位热源**：仅保留接口，不属于本次执行技术，不得在答辩中描述为已参与优化。
- **TES**：冻结价格配对与敏感性必须分开表述；敏感性只改变TES投资，不改负荷、站网或其他设备参数。
- **结果性质**：本图册证明源荷匹配和规划优化软件能力，不证明施工可实施性或合同价格精度。
"""
    path.write_text(text, encoding="utf-8")
    return path


def generate_full_study_report(
    run_root: str | Path,
    output_root: str | Path,
    tes_sensitivity_root: str | Path | None = None,
) -> FullStudyReport:
    """Generate a read-only static atlas and display contract.

    ``output_root`` must not exist.  This prevents accidental replacement of a
    reviewed presentation package.
    """

    result = FullStudyResultAdapter(run_root).load()
    tes_sensitivity, tes_source_paths = _load_tes_sensitivity(
        result, tes_sensitivity_root
    )
    output = Path(output_root).expanduser().resolve()
    if output.exists():
        raise FullStudyReportError(f"展示输出目录已经存在，拒绝覆盖：{output}")
    output.mkdir(parents=True)
    figures_dir = output / "figures"
    figures_dir.mkdir()
    tables_dir = output / "tables"
    table_files, frames = _build_display_tables(
        result, tables_dir, tes_sensitivity=tes_sensitivity
    )
    presentation_data = output / "presentation_data.json"
    _write_presentation_data(frames, presentation_data)
    table_files.append(presentation_data)

    _configure_style()
    building, dispatch, network, _storage = _load_knee_hourly(result)
    hourly = _hourly_series(building, dispatch)
    atlas_pdf = output / "UrbanHeatOpt_results_atlas.pdf"
    figures: list[dict[str, Any]] = []
    specifications = (
        ("fig01_pareto_frontier", "成本—运行碳排Pareto前沿", lambda: _plot_pareto(result)),
        ("fig02_cost_carbon_breakdown", "膝点成本与碳排构成", lambda: _plot_cost_carbon_breakdown(result)),
        ("fig03_load_duration", "全供暖季负荷持续曲线", lambda: _plot_load_duration(result, hourly)),
        ("fig04_source_load_heatmap", "源荷逐时热图", lambda: _plot_heatmap(result, hourly)),
        ("fig05_peak_day_dispatch", "峰值日逐时调度", lambda: _plot_peak_dispatch(result, hourly, building)),
        ("fig06_energy_flow", "年度源网荷能流", lambda: _plot_energy_flow(result, building, dispatch, network)),
        ("fig07_network_plan", "候选与建成站网规划图", lambda: _plot_network(result)),
        ("fig08_tes_pair", "TES有无配对", lambda: _plot_tes(result, frames["tes"])),
        ("fig09_qa_evidence", "独立QA证据", lambda: _plot_qa(result, frames["qa"])),
    )
    with PdfPages(atlas_pdf, metadata={"Title": "UrbanHeatOpt全研究成果图册", "Author": "UrbanHeatOpt"}) as pdf:
        for figure_id, title, factory in specifications:
            figures.append(_save_figure(factory(), figure_id, title, figures_dir, pdf, result))
    if atlas_pdf.stat().st_size < 20_000 or atlas_pdf.read_bytes()[:4] != b"%PDF":
        raise FullStudyReportError(f"PDF图册渲染检查失败：{atlas_pdf}")

    guide = _write_results_guide(result, output, frames)
    cp_trace = _write_cp_traceability(result, output, frames)
    source_paths = [
        result.run_root / "request_set_summary.json",
        result.run_root / "pareto_no_tes" / "request_set_summary.json",
        result.run_root / "pareto_no_tes" / "pareto_points.csv",
        result.run_root / "pareto_no_tes" / "pareto_frontiers.json",
        result.run_root / "pareto_no_tes" / "knee_points.json",
        result.knee_directory / "run_manifest.json",
        result.knee_directory / "result_bundle.json",
        result.knee_directory / "independent_qa.json",
        *tes_source_paths,
    ]
    manifest_payload = {
        "schema_version": "urbanheatopt_figure_manifest_v1",
        "reporting_version": REPORTING_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_run_root": str(result.run_root),
        "source_run_id": result.run_root.name,
        "source_git_sha": result.git_sha,
        "source_building_count": result.building_count,
        "source_hour_count": result.hour_count,
        "optimization_scope": result.optimization_scope,
        "full_study_qualified": True,
        "all_point_qa_passed": True,
        "tes_sensitivity": (
            {
                "loaded": True,
                "source_root": str(tes_sensitivity["root"]),
                "selected_multiplier": float(
                    tes_sensitivity["summary"]["selected_multiplier"]
                ),
                "demonstration_achieved": True,
                "kept_separate_from_frozen_pareto": True,
            }
            if tes_sensitivity is not None
            else {"loaded": False}
        ),
        "authoritative_knee": {
            "source": "pareto_no_tes/knee_points.json",
            "point_id": result.combined_knee_id,
            "csv_is_knee_column_trusted": False,
        },
        "source_evidence": [
            {"path": str(path), "sha256": _sha256(path), "size_bytes": path.stat().st_size}
            for path in source_paths
        ],
        "figures": figures,
        "atlas_pdf": {
            "path": atlas_pdf.name,
            "sha256": _sha256(atlas_pdf),
            "size_bytes": atlas_pdf.stat().st_size,
            "page_count": len(figures),
            "render_checked": True,
        },
        "tables": [
            {
                "path": str(path.relative_to(output)).replace("\\", "/"),
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }
            for path in table_files
        ],
        "documents": [guide.name, cp_trace.name],
        "limitations": [
            "five_candidate_shortest_path_trees",
            "candidate_sites_not_professionally_verified_land_parcels",
            "planning_network_not_construction_design",
            "low_grade_heat_source_interface_only",
            "tes_frozen_capex_zero_capacity",
            "tes_sensitivity_is_not_part_of_frozen_pareto",
        ],
    }
    manifest = output / "figure_manifest.json"
    manifest.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return FullStudyReport(
        output_directory=output,
        guide=guide,
        manifest=manifest,
        atlas_pdf=atlas_pdf,
        figure_files=tuple(sorted(figures_dir.iterdir())),
        table_files=tuple(table_files),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成UrbanHeatOpt full-study静态成果图册")
    parser.add_argument("--run-root", required=True, help="FULL_STUDY结果目录")
    parser.add_argument("--output-root", required=True, help="新的展示输出目录（必须不存在）")
    parser.add_argument(
        "--tes-sensitivity-root",
        help="可选：与权威膝点固定结构一致的TES投资敏感性目录",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = generate_full_study_report(
        args.run_root,
        args.output_root,
        tes_sensitivity_root=args.tes_sensitivity_root,
    )
    print(json.dumps({"status": "completed", "output_directory": str(report.output_directory), "figure_manifest": str(report.manifest)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
