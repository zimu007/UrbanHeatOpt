"""Single competition entry pipeline; never calls the legacy optimisation model."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from typing import Any

from pyomo.environ import value

from competition.core_model import solve_core_model
from competition.pareto import ParetoSpec, point_to_dict, solve_case_pareto
from competition.results import export_v3_results
from competition.validation.v3_inputs import load_v3_case
from competition.adapters.wuhan_v02_case import prepare_wuhan_v02_v0_case


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
    return f"{case_id}-{scenario_id}-{sha256(joined.encode('utf-8')).hexdigest()[:12]}"


def _git_sha() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[2],
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
) -> PipelineRun:
    """Validate, snapshot, solve all three modes and export an integration summary."""

    case = load_v3_case(case_dir, profile=profile)
    run_id = _run_id(case.case_id, case.scenario_id, dict(case.input_sha256))
    output = Path(output_root).resolve() / case.case_id / case.scenario_id / run_id
    output.mkdir(parents=True, exist_ok=True)

    mode_results: list[dict[str, Any]] = []
    for mode in case.modes:
        solved = solve_core_model(case.to_core_input(mode), case.solver)
        model = solved.model
        mode_results.append(
            {
                "mode": mode,
                "peak_capacity_margin_fraction": case.peak_capacity_margin_fraction,
                "termination_condition": str(solved.solver_results.solver.termination_condition),
                "annual_real_cost_CNY_per_year": float(value(model.annual_real_cost_CNY_per_year)),
                "annual_hns_penalty_CNY_per_year": float(value(model.annual_hns_penalty_CNY_per_year)),
                "optimization_objective_CNY_per_year": float(value(model.optimization_objective_CNY_per_year)),
                "annual_operating_physical_carbon_kgCO2e_per_year": float(
                    value(model.annual_operating_physical_carbon_kgCO2e_per_year)
                ),
                "annual_policy_carbon_cost_CNY_per_year": float(
                    value(model.annual_policy_carbon_cost_CNY_per_year)
                ),
                "unserved_heat_kWh": float(
                    sum(
                        value(model.unserved_heat_kW[node, hour])
                        for node in model.DEMAND_NODES
                        for hour in model.HOURS
                    )
                ),
                "connections": {
                    str(node): int(round(value(model.connected[node])))
                    for node in model.DEMAND_NODES
                },
            }
        )

    pareto_config = case.raw_config.get("pareto", {})
    qa_config = case.raw_config.get("qa", {})
    pareto = solve_case_pareto(
        case,
        ParetoSpec(
            point_count=int(pareto_config.get("point_count", 5)),
            unserved_tolerance_kWh=float(qa_config.get("unserved_tolerance_kWh", 1e-6)),
            cost_tolerance_CNY_per_year=float(qa_config.get("cost_tolerance_CNY_per_year", 1e-6)),
            carbon_tolerance_kgCO2e_per_year=float(qa_config.get("carbon_tolerance_kgCO2e_per_year", 1e-6)),
        ),
    )
    standard_export = export_v3_results(case, pareto, output, case_dir)

    manifest = {
        "run_id": run_id,
        "software_release": "UrbanHeatOpt-test-V0.x",
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
        "capability_status": {
            "unified_core": "implemented",
            "economics": "crf_annualization_implemented",
            "storage": "linear_core_and_cyclic_soc_implemented",
            "temperature_cop": (
                f"hourly_provider_active:{case.heat_pump_performance.provider_name}"
            ),
            "discrete_pipe_capacity": "three_levels_implemented",
            "pipe_loss": "linear_core_implemented_disabled_by_v0_input",
            "pumping": "linear_core_cost_carbon_implemented_disabled_by_v0_input",
            "candidate_generation": "provider_interface_only_not_executable",
            "carbon": "operating_physical_carbon_implemented",
            "pareto": "epsilon_constraint_implemented",
        },
        "standard_results": {
            "pareto_points": standard_export.pareto_csv.name,
            "candidate_sites": standard_export.candidate_sites_geojson.name,
            "qa_summary": standard_export.qa_summary_json.name,
            "solutions_directory": "solutions",
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
