import json
from pathlib import Path

import pandas as pd
import pytest

from urbanheatopt.data.bundles import (
    INTERFACE_VERSION,
    CaseBundle,
    SolveRequest,
    sha256_file,
)
from urbanheatopt.optimization.solve_executor import (
    MODEL_PROFILE,
    execute_pareto_knee_set,
    execute_request,
    execute_request_set,
    validate_run_id,
)
import urbanheatopt.optimization.solve_executor as solve_executor

from test_road_v2_core import shared_case


def _case_bundle(tmp_path: Path) -> CaseBundle:
    artifact = tmp_path / "case_source.json"
    artifact.write_text('{"synthetic":true}\n', encoding="utf-8")
    return CaseBundle.from_dict({
        "interface_version": INTERFACE_VERSION,
        "data_version": "synthetic_executor_test",
        "parameter_version": "synthetic_executor_parameters",
        "git_sha": "synthetic_test",
        "artifacts": [{
            "role": "synthetic_case_source",
            "path": str(artifact.resolve()),
            "sha256": sha256_file(artifact),
        }],
        "source_hashes": {str(artifact.resolve()): sha256_file(artifact)},
        "units": {"heating_kW": "kW_th"},
        "capabilities_required": [],
        "status": {
            "input_valid": True,
            "parameter_valid": True,
            "canonical_valid": True,
            "snapshot_complete": True,
        },
    })


def _request(bundle: CaseBundle, mode: str) -> SolveRequest:
    return SolveRequest.from_dict({
        "interface_version": INTERFACE_VERSION,
        "case_bundle_id": bundle.bundle_id,
        "model_profile": MODEL_PROFILE,
        "optimization_scope": "five_candidate_shortest_path_trees",
        "mode": mode,
        "objective": "cost",
        "epsilon_carbon_kg": None,
        "tes_enabled": False,
        "allow_unserved": False,
        "solver": {
            "name": "highs",
            "threads": 1,
            "random_seed": 202611,
            "mip_gap": 0.01,
            "time_limit_s": 30,
        },
    })


@pytest.mark.parametrize("mode", ["central", "distributed", "hybrid"])
def test_same_executor_solves_all_modes_and_exports_certified_results(tmp_path, mode):
    bundle = _case_bundle(tmp_path)
    output = tmp_path / f"run_{mode}"
    result = execute_request(bundle, shared_case(mode), _request(bundle, mode), output)

    assert result.to_dict()["qualified"] is True
    for name in (
        "result_bundle.json",
        "run_manifest.json",
        "solver_evidence.json",
        "candidate_site_comparison.csv",
        "selected_site.json",
        "capacity_decisions.csv",
        "building_connection.csv",
        "network_decisions.geojson",
        "dispatch_hourly.parquet",
        "cost_breakdown.csv",
        "carbon_breakdown.csv",
        "independent_qa.json",
    ):
        assert (output / name).is_file(), name
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["legacy_fallback_used"] is False
    assert manifest["model_profile"] == MODEL_PROFILE
    assert manifest["mode"] == mode
    assert manifest["result_qualified"] is True
    comparison = pd.read_csv(output / "candidate_site_comparison.csv")
    assert comparison["status"].eq("qualified").all()
    assert comparison["selected"].sum() == 1
    connections = pd.read_csv(output / "building_connection.csv")
    if mode == "central":
        assert connections["connected"].eq(1).all()
    elif mode == "distributed":
        assert connections["connected"].eq(0).all()


def test_executor_rejects_request_for_another_bundle_before_output(tmp_path):
    bundle = _case_bundle(tmp_path)
    payload = _request(bundle, "central").to_dict()
    payload["case_bundle_id"] = "a" * 64
    request = SolveRequest.from_dict(payload)
    target = tmp_path / "must_not_exist"
    with pytest.raises(ValueError, match="CaseBundle不一致"):
        execute_request(bundle, shared_case(), request, target)
    assert not target.exists()


def test_executor_never_overwrites_existing_run_directory(tmp_path):
    bundle = _case_bundle(tmp_path)
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        execute_request(bundle, shared_case(), _request(bundle, "central"), target)
    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep"

@pytest.mark.parametrize("value", ["", "bad space", "../bad", "a" * 101])
def test_run_id_is_path_safe(value):
    with pytest.raises(ValueError):
        validate_run_id(value)


def _prepared_requests(tmp_path: Path, bundle: CaseBundle) -> Path:
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    bundle_path = prepared / "case_bundle.json"
    bundle.write(bundle_path)
    requests = prepared / "solve_requests"
    requests.mkdir()
    for mode in ("central", "distributed", "hybrid"):
        _request(bundle, mode).write(requests / f"{mode}_cost.json")
    return bundle_path


def test_pareto_request_set_runs_real_tiny_models_and_never_fabricates_knee(
    tmp_path, monkeypatch
):
    bundle = _case_bundle(tmp_path)
    bundle_path = _prepared_requests(tmp_path, bundle)
    monkeypatch.setattr(
        solve_executor,
        "load_prepared_road_case",
        lambda _path: (bundle, shared_case(), {"synthetic": True}),
    )
    output = tmp_path / "pareto"
    result = execute_pareto_knee_set(bundle_path, output)
    assert result["qualified"] is True
    assert result["method"] == "epsilon_constraint"
    assert (output / "pareto_points.csv").is_file()
    assert (output / "pareto_frontiers.json").is_file()
    knees = json.loads((output / "knee_points.json").read_text(encoding="utf-8"))
    assert knees["no_fabricated_knee_when_fewer_than_three_distinct_points"] is True
    points = pd.read_csv(output / "pareto_points.csv")
    assert set(points["mode"]) == {"central", "distributed", "hybrid"}
    assert points["unserved_heat_kWh"].abs().max() <= 1e-6


def test_full_study_fails_closed_until_tes_pair_executor_exists(tmp_path):
    target = tmp_path / "not_created"
    with pytest.raises(ValueError, match="TES固定结构配对"):
        execute_request_set(tmp_path / "missing.json", target, "full-study")
    assert not target.exists()
