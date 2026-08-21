"""Single competition entry pipeline; never calls the legacy optimisation model."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from typing import Any

from pyomo.environ import value

from competition.core_model import solve_core_model
from competition.validation.v3_inputs import load_v3_case


@dataclass(frozen=True, slots=True)
class PipelineRun:
    output_dir: Path
    manifest_path: Path
    summary_path: Path
    manifest: dict[str, Any]


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
            "storage": "interface_only",
            "temperature_cop": "interface_only",
            "pipe_loss": "interface_only",
            "pumping": "interface_only",
            "carbon": "operating_physical_carbon_implemented",
            "pareto": "pending",
        },
    }
    manifest_path = output / "run_manifest.json"
    summary_path = output / "mode_summary.json"
    _write_json(manifest_path, manifest)
    _write_json(summary_path, mode_results)
    return PipelineRun(output, manifest_path, summary_path, manifest)
