from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

from urbanheatopt.data.bundles import (
    INTERFACE_VERSION,
    CaseBundle,
    SolveRequest,
    sha256_file,
)
from urbanheatopt.data.integration import generate_solve_requests
from urbanheatopt.data.road_builder import save_case
from urbanheatopt.optimization import solve_executor

from test_road_v2_core import shared_case


def _bundle(tmp_path: Path) -> CaseBundle:
    artifact = tmp_path / "source.json"
    artifact.write_text('{"synthetic":true}\n', encoding="utf-8")
    return CaseBundle.from_dict({
        "interface_version": INTERFACE_VERSION,
        "data_version": "synthetic_parallel_test",
        "parameter_version": "synthetic_parallel_parameters",
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


def _request(bundle: CaseBundle, *, workers: str | int) -> SolveRequest:
    return SolveRequest.from_dict({
        "interface_version": INTERFACE_VERSION,
        "case_bundle_id": bundle.bundle_id,
        "model_profile": solve_executor.MODEL_PROFILE,
        "optimization_scope": "five_candidate_shortest_path_trees",
        "mode": "central",
        "objective": "cost",
        "epsilon_carbon_kg": None,
        "tes_enabled": False,
        "allow_unserved": False,
        "solver": {
            "name": "highs",
            "threads": 1,
            "candidate_workers": workers,
            "random_seed": 202611,
            "mip_gap": 0.01,
            "time_limit_s": 30,
            "presolve": "on",
        },
    })


def test_generated_requests_use_configured_threads_and_parallel_policy(tmp_path):
    bundle = _bundle(tmp_path)
    output = tmp_path / "prepared"
    output.mkdir()
    rows = generate_solve_requests(
        bundle,
        output,
        tes_enabled=False,
        solver_config={
            "name": "highs",
            "threads": 4,
            "candidate_workers": "auto",
            "random_seed": 202611,
            "mip_gap": 0.01,
            "time_limit_s": None,
            "presolve": "on",
        },
    )
    assert len(rows) == 3
    for _mode, request, path in rows:
        solver = request.to_dict()["solver"]
        assert solver["threads"] == 4
        assert solver["candidate_workers"] == "auto"
        assert solver["presolve"] == "on"
        assert SolveRequest.read(path).to_dict()["solver"] == solver


def test_auto_candidate_worker_plan_is_resource_bounded(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path)
    payload = _request(bundle, workers="auto").to_dict()
    payload["solver"]["threads"] = 4
    case_path = tmp_path / "road_case.json"
    case_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(solve_executor.os, "cpu_count", lambda: 28)
    monkeypatch.setattr(
        solve_executor,
        "_available_memory_bytes",
        lambda: 32 * 1024**3,
    )
    plan = solve_executor._candidate_execution_plan(
        payload, 5, {"road_case_path": str(case_path)},
    )
    assert plan["resolved_candidate_workers"] == 3
    assert plan["threads_per_candidate"] == 4
    assert plan["nested_parallelism"] is False
    assert plan["process_isolation"] is True


def test_explicit_serial_candidate_policy_needs_no_serialized_case(tmp_path):
    bundle = _bundle(tmp_path)
    plan = solve_executor._candidate_execution_plan(
        _request(bundle, workers=1).to_dict(), 5, None,
    )
    assert plan["resolved_candidate_workers"] == 1
    assert plan["limit_reasons"] == ["explicit_serial"]
    assert plan["process_isolation"] is False


def test_two_candidate_processes_write_isolated_stable_results(tmp_path):
    bundle = _bundle(tmp_path)
    case = shared_case("central")
    case_path = tmp_path / "road_case.json"
    save_case(case, case_path)
    output = tmp_path / "parallel_run"
    result = solve_executor.execute_request(
        bundle,
        case,
        _request(bundle, workers=2),
        output,
        road_case_evidence={"road_case_path": str(case_path)},
    )
    assert result.to_dict()["qualified"] is True
    plan = json.loads(
        (output / "candidate_execution_plan.json").read_text(encoding="utf-8")
    )
    assert plan["resolved_candidate_workers"] == 2
    comparison = pd.read_csv(output / "candidate_site_comparison.csv")
    assert comparison["site_id"].tolist() == ["S1", "S2"]
    assert comparison["status"].eq("qualified").all()
    assert comparison["worker_pid"].notna().all()
    assert not comparison["worker_pid"].eq(os.getpid()).any()
    for site_id in ("S1", "S2"):
        assert (output / "candidate_tasks" / site_id / "task_status.json").is_file()
