"""Single competition entry pipeline; never calls the legacy optimisation model."""

from __future__ import annotations

from urbanheatopt.paths import REPOSITORY_ROOT as REPOSITORY_ROOT_PATH, PACKAGE_ROOT

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

import geopandas as gpd
import pandas as pd

from urbanheatopt.optimization.pareto import ParetoSpec, point_to_dict, solve_case_pareto
from urbanheatopt.reporting.results import export_v3_results, export_v3_solution
from urbanheatopt.data.validation.v3_inputs import load_v3_case
from urbanheatopt.data.adapters.wuhan_v02_case import prepare_wuhan_v02_v0_case
from urbanheatopt.data.adapters.guanggu_v03_case import prepare_guanggu_v03_v0_case
from urbanheatopt.data.readiness import GuangguV03InputValidationRun
from urbanheatopt.optimization.solvers import SolverNotOptimalError


@dataclass(frozen=True, slots=True)
class PipelineRun:
    output_dir: Path
    manifest_path: Path
    summary_path: Path
    manifest: dict[str, Any]
    pareto_path: Path | None = None


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _run_id(case_id: str, scenario_id: str, input_hashes: dict[str, str]) -> str:
    joined = "|".join(f"{name}:{digest}" for name, digest in sorted(input_hashes.items()))
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f%z")
    return f"{timestamp}-{case_id}-{scenario_id}-{sha256(joined.encode('utf-8')).hexdigest()[:12]}"


def _validated_run_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("run_id 只能包含字母、数字、点、下划线和连字符，且最长128字符")
    return value


def _git_sha() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT_PATH,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def run_case_pipeline(
    case_dir: str | Path,
    *,
    profile: str,
    output_root: str | Path = "runs",
    run_id: str | None = None,
) -> PipelineRun:
    """Validate, snapshot, solve all three modes and export an integration summary."""

    case = load_v3_case(case_dir, profile=profile)
    resolved_run_id = (
        _validated_run_id(run_id)
        if run_id is not None
        else _run_id(case.case_id, case.scenario_id, dict(case.input_sha256))
    )
    output = Path(output_root).resolve() / case.case_id / case.scenario_id / resolved_run_id
    output.mkdir(parents=True, exist_ok=False)
    pipeline_started = datetime.now(timezone.utc)
    pipeline_clock = perf_counter()

    pareto_config = case.raw_config.get("pareto", {})
    qa_config = case.raw_config.get("qa", {})
    run_epsilon_scan = bool(pareto_config.get("run_epsilon_scan", True))
    network_input = gpd.read_file(
        Path(case_dir) / case.raw_config["files"]["candidate_network"]
    )
    sites_input = gpd.read_file(
        Path(case_dir) / case.raw_config["files"]["candidate_sites"]
    )

    def stream_solution(point: Any, solution: Any) -> None:
        export_v3_solution(
            case, point, solution, output, network_input, sites_input
        )

    try:
        pareto = solve_case_pareto(
            case,
            ParetoSpec(
                point_count=(
                    int(pareto_config.get("point_count", 5))
                    if run_epsilon_scan
                    else 0
                ),
                unserved_tolerance_kWh=float(qa_config.get("unserved_tolerance_kWh", 1e-6)),
                cost_tolerance_CNY_per_year=float(qa_config.get("cost_tolerance_CNY_per_year", 1e-6)),
                carbon_tolerance_kgCO2e_per_year=float(qa_config.get("carbon_tolerance_kgCO2e_per_year", 1e-6)),
            ),
            solution_callback=stream_solution,
            retain_solutions=False,
        )
    except Exception as exc:
        finished = datetime.now(timezone.utc)
        failure: dict[str, Any] = {
            "run_id": resolved_run_id,
            "status": "failed_before_complete_export",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "started_at_utc": pipeline_started.isoformat(),
            "finished_at_utc": finished.isoformat(),
            "elapsed_seconds": perf_counter() - pipeline_clock,
            "git_commit": _git_sha(),
            "contract_version": case.contract_version,
            "data_version": case.data_version,
            "profile": case.profile,
            "input_sha256": dict(case.input_sha256),
            "legacy_model_used": False,
            "solver_executed": isinstance(exc, SolverNotOptimalError),
        }
        if isinstance(exc, SolverNotOptimalError):
            failure["solver"] = {
                "name": exc.solver_name,
                "status": exc.solver_status,
                "termination_condition": exc.termination_condition,
                "reported_mip_gap": exc.reported_mip_gap,
                "incumbent_objective": exc.incumbent_objective,
                "best_objective_bound": exc.best_objective_bound,
                "has_feasible_solution": exc.has_feasible_solution,
                "model_sha256": exc.model_sha256,
                "solver_log_file": exc.solver_log_file,
                "solver_evidence_file": exc.solver_evidence_file,
                "mip_gap_target": exc.mip_gap_target,
                "time_limit_seconds": exc.time_limit_seconds,
                "threads": exc.threads,
                "random_seed": exc.random_seed,
                "solution_values_loaded": False,
            }
        _write_json(output / "run_failure.json", failure)
        raise
    standard_export = export_v3_results(case, pareto, output, case_dir)
    mode_results: list[dict[str, Any]] = []
    for mode in case.modes:
        cost_point = next(
            point
            for point in pareto.mode_all_points[mode]
            if "cost_endpoint" in point.labels
        )
        solution_dir = output / "solutions" / cost_point.point_id
        cost_table = pd.read_csv(solution_dir / "cost_breakdown.csv")
        costs = dict(
            zip(
                cost_table["category"].astype(str),
                cost_table["annual_cost_CNY_per_year"].astype(float),
                strict=True,
            )
        )
        connections = pd.read_csv(solution_dir / "building_connection.csv")
        mode_results.append(
            {
                "mode": mode,
                "source_pareto_point_id": cost_point.point_id,
                "peak_capacity_margin_fraction": case.peak_capacity_margin_fraction,
                "solver_status": cost_point.solver_status,
                "termination_condition": cost_point.termination_condition,
                "started_at_utc": cost_point.solve_started_at_utc,
                "finished_at_utc": cost_point.solve_finished_at_utc,
                "elapsed_seconds": cost_point.solve_elapsed_seconds,
                "reported_wallclock_seconds": cost_point.reported_wallclock_seconds,
                "reported_mip_gap": cost_point.reported_mip_gap,
                "annual_real_cost_CNY_per_year": cost_point.annual_real_cost_CNY_per_year,
                "annual_hns_penalty_CNY_per_year": cost_point.annual_hns_penalty_CNY_per_year,
                "optimization_objective_CNY_per_year": (
                    cost_point.annual_real_cost_CNY_per_year
                    + cost_point.annual_hns_penalty_CNY_per_year
                ),
                "annual_operating_physical_carbon_kgCO2e_per_year": (
                    cost_point.annual_operating_carbon_kgCO2e_per_year
                ),
                "annual_policy_carbon_cost_CNY_per_year": costs.get(
                    "policy_carbon_cost", 0.0
                ),
                "unserved_heat_kWh": cost_point.unserved_heat_kWh,
                "connections": {
                    str(row.building_id): int(round(float(row.connected)))
                    for row in connections.itertuples(index=False)
                },
            }
        )
    pipeline_finished = datetime.now(timezone.utc)
    pipeline_elapsed = perf_counter() - pipeline_clock

    manifest = {
        "run_id": resolved_run_id,
        "software_release": (
            "UrbanHeatOpt-V1.0"
            if case.software_release_track == "formal_v1"
            else "UrbanHeatOpt-test-V0.x"
        ),
        "software_release_track": case.software_release_track,
        "git_commit": _git_sha(),
        "contract_version": case.contract_version,
        "case_id": case.case_id,
        "scenario_id": case.scenario_id,
        "data_version": case.data_version,
        "profile": case.profile,
        "modes": list(case.modes),
        "input_sha256": dict(case.input_sha256),
        "parameter_versions": dict(case.parameter_versions),
        "legacy_model_used": False,
        "execution": {
            "started_at_utc": pipeline_started.isoformat(),
            "finished_at_utc": pipeline_finished.isoformat(),
            "elapsed_seconds": pipeline_elapsed,
            "solver_name": case.solver.name,
            "pareto_execution": (
                "endpoints_and_epsilon_scan"
                if run_epsilon_scan
                else "endpoints_only"
            ),
            "threads": case.solver.threads,
            "time_limit_seconds": case.solver.time_limit_seconds,
            "mip_gap_target": case.solver.mip_gap,
            "random_seed": case.solver.random_seed,
        },
        "capability_status": {
            "unified_core": "implemented",
            "economics": "crf_annualization_implemented",
            "storage": "linear_core_and_cyclic_soc_implemented",
            "temperature_cop": (
                f"hourly_provider_active:{case.heat_pump_performance.provider_name}"
            ),
            "discrete_pipe_capacity": "three_levels_implemented",
            "pipe_loss": (
                "linear_flow_proportional_core_active"
                if case.raw_config.get("features", {}).get("pipe_loss_enabled", False)
                else "linear_core_implemented_disabled_by_input"
            ),
            "pumping": (
                "linear_core_cost_carbon_active"
                if case.raw_config.get("features", {}).get("pumping_enabled", False)
                else "linear_core_cost_carbon_implemented_disabled_by_input"
            ),
            "candidate_generation": "provider_interface_only_not_executable",
            "carbon": "operating_physical_carbon_implemented",
            "pareto": "epsilon_constraint_implemented",
        },
        "standard_results": {
            "pareto_points": standard_export.pareto_csv.name,
            "combined_nondominated_frontier": (
                standard_export.combined_frontier_csv.name
                if standard_export.combined_frontier_csv is not None
                else None
            ),
            "representative_solutions": (
                standard_export.representatives_json.name
                if standard_export.representatives_json is not None
                else None
            ),
            "candidate_sites": standard_export.candidate_sites_geojson.name,
            "qa_summary": standard_export.qa_summary_json.name,
            "solutions_directory": "solutions",
            "figures": [
                str(path.relative_to(output)) for path in standard_export.figure_paths
            ],
        },
    }
    manifest_path = output / "run_manifest.json"
    summary_path = output / "mode_summary.json"
    pareto_path = output / "pareto_points.json"
    _write_json(manifest_path, manifest)
    _write_json(summary_path, mode_results)
    _write_json(
        pareto_path,
        {
            "by_mode": {
                mode: [point_to_dict(point) for point in points]
                for mode, points in pareto.mode_frontiers.items()
            },
            "combined": [point_to_dict(point) for point in pareto.combined_frontier],
        },
    )
    return PipelineRun(output, manifest_path, summary_path, manifest, pareto_path)


def run_wuhan_v02_pipeline(
    delivery_root: str | Path,
    *,
    source_profile: str,
    assumption_profile: str,
    profile: str,
    output_root: str | Path = "runs",
    run_id: str | None = None,
) -> PipelineRun:
    """Audit/prepare Guanggu v0.2 and call the same unified new-core pipeline."""

    with TemporaryDirectory(prefix="urbanheatopt-wuhan-v02-") as temporary:
        prepared = prepare_wuhan_v02_v0_case(
            delivery_root,
            temporary,
            source_profile=source_profile,
            assumption_profile=assumption_profile,
            profile=profile,
        )
        result = run_case_pipeline(
            prepared.case_dir,
            profile=profile,
            output_root=output_root,
            run_id=run_id,
        )
        for name in (
            "source_validation_report.json",
            "adaptation_report.json",
            "field_mapping.csv",
            "assumptions_used.yaml",
        ):
            shutil.copy2(prepared.case_dir / name, result.output_dir / name)
        snapshot = result.output_dir / "standardized_input_snapshot"
        snapshot.mkdir(exist_ok=True)
        snapshot_names = {"case_config.yaml"}
        snapshot_names.update(
            name
            for name in prepared.canonical_case.raw_config["files"].values()
            if isinstance(name, str)
        )
        for name in sorted(snapshot_names):
            shutil.copy2(prepared.case_dir / name, snapshot / name)
        manifest = dict(result.manifest)
        manifest.update(
            {
                "source_profile": source_profile,
                "assumption_profile": assumption_profile,
                "result_classification": "weighted_period_test",
                "selected_building_ids": list(prepared.scope.building_ids),
                "selected_peak_day": prepared.scope.peak_day,
                "delivery_root_stored": False,
                "standardized_input_snapshot": snapshot.name,
            }
        )
        manifest["capability_status"] = dict(manifest["capability_status"])
        manifest["capability_status"]["candidate_generation"] = (
            "provisional_geometric_mst_non_road_non_construction"
        )
        _write_json(result.manifest_path, manifest)
        return PipelineRun(
            result.output_dir,
            result.manifest_path,
            result.summary_path,
            manifest,
            result.pareto_path,
        )


def run_guanggu_v03_pipeline(
    validation_run: GuangguV03InputValidationRun,
    *,
    assumption_profile: str,
    profile: str,
    output_root: str | Path = "runs",
    run_id: str | None = None,
) -> PipelineRun:
    """Build a V0 v0.3 case and execute the same unified new-core pipeline."""

    with TemporaryDirectory(prefix="urbanheatopt-guanggu-v03-") as temporary:
        prepared = prepare_guanggu_v03_v0_case(
            validation_run.adaptation,
            temporary,
            assumption_profile=assumption_profile,
            profile=profile,
        )
        result = run_case_pipeline(
            prepared.case_dir,
            profile=profile,
            output_root=output_root,
            run_id=run_id,
        )
        evidence_names = (
            "source_validation_report.json",
            "canonical_validation_report.json",
            "adaptation_report.json",
            "field_mapping.csv",
            "timestamp_hour_map.csv",
            "assumptions_used.yaml",
        )
        for name in evidence_names:
            source = prepared.case_dir / name
            if source.is_file():
                shutil.copy2(source, result.output_dir / name)
        shutil.copy2(
            validation_run.readiness_report_path,
            result.output_dir / "model_readiness_report_at_run.json",
        )
        snapshot = result.output_dir / "standardized_input_snapshot"
        snapshot.mkdir()
        snapshot_names = {"case_config.yaml", "equipment_performance.csv"}
        snapshot_names.update(
            name
            for name in prepared.canonical_case.raw_config["files"].values()
            if isinstance(name, str)
        )
        for name in sorted(snapshot_names):
            source = prepared.case_dir / name
            if source.is_file():
                shutil.copy2(source, snapshot / name)
        manifest = dict(result.manifest)
        manifest.update(
            {
                "source_profile": "guanggu_v03",
                "assumption_profile": assumption_profile,
                "result_classification": (
                    "full_season_v0_software_validation"
                    if profile == "v0-full-season"
                    else "weighted_period_test"
                ),
                "selected_building_ids": list(prepared.scope.building_ids),
                "selected_hour_count": len(prepared.scope.timestamps),
                "selection_rule": prepared.scope.selection_rule,
                "full_park_peak_timestamp": prepared.scope.peak_timestamp,
                "source_input_sha256": dict(
                    validation_run.adaptation.source_report.file_sha256
                ),
                "source_input_file_count": len(
                    validation_run.adaptation.source_report.file_sha256
                ),
                "delivery_root_stored": False,
                "standardized_input_snapshot": snapshot.name,
                "formal_engineering_conclusion_allowed": False,
            }
        )
        manifest["capability_status"] = dict(manifest["capability_status"])
        manifest["capability_status"].update(
            {
                "candidate_generation": "provisional_five_site_non_road_non_construction",
                "source_validation": "v03_plus_0821_lhv_patch_passed",
                "temperature_cop": (
                    f"hourly_provider_active:{prepared.canonical_case.heat_pump_performance.provider_name}"
                ),
            }
        )
        _write_json(result.manifest_path, manifest)
        return PipelineRun(
            result.output_dir,
            result.manifest_path,
            result.summary_path,
            manifest,
            result.pareto_path,
        )
