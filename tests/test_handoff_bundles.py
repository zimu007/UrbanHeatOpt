"""A/B/C interface tests; no optimization or real project data are used."""

import copy
import json
from dataclasses import FrozenInstanceError

import pytest

from urbanheatopt.data.bundles import (
    INTERFACE_VERSION, BundleValidationError, CaseBundle, ResultBundle,
    SolveRequest, ready_report, sha256_file,
)


@pytest.fixture
def case_payload(tmp_path):
    artifact = tmp_path / "loads.json"
    artifact.write_text('{"synthetic_test":true,"heat_kW":100}', encoding="utf-8")
    source = tmp_path / "source.json"
    source.write_text('{"source":"synthetic_test"}', encoding="utf-8")
    return {
        "interface_version": INTERFACE_VERSION,
        "data_version": "synthetic_test",
        "parameter_version": "synthetic_parameters_1",
        "git_sha": "test_commit_only",
        "artifacts": [{"role": "loads", "path": str(artifact), "sha256": sha256_file(artifact)}],
        "source_hashes": {str(source): sha256_file(source)},
        "units": {"heat": "kW_th", "gas": "kWh_LHV"},
        "capabilities_required": ["monthly_demand_charge", "effective_parameter_mapping", "result_bundle"],
        "status": {"input_valid": True, "parameter_valid": True, "canonical_valid": True, "snapshot_complete": True},
    }


def solve_payload(case_payload):
    return {
        "interface_version": INTERFACE_VERSION,
        "case_bundle_id": CaseBundle.from_dict(case_payload).bundle_id,
        "mode": "hybrid", "objective": "cost", "epsilon_carbon_kg": 1000.0,
        "tes_enabled": False,
        "solver": {"name": "highs", "threads": 1, "random_seed": 202611, "mip_gap": 0.01, "time_limit_s": None},
    }


def test_bundle_freezes_nested_caller_and_returned_data(case_payload):
    bundle = CaseBundle.from_dict(case_payload)
    original_id = bundle.bundle_id
    case_payload["units"]["heat"] = "MW"
    exposed = bundle.payload
    exposed["artifacts"][0]["sha256"] = "0" * 64
    assert bundle.to_dict()["units"]["heat"] == "kW_th"
    assert bundle.bundle_id == original_id
    with pytest.raises((FrozenInstanceError, TypeError, AttributeError)):
        bundle._json = "{}"


def test_hash_canonical_order_and_parameter_change(case_payload):
    initial = CaseBundle.from_dict(case_payload)
    assert initial.bundle_id == CaseBundle.from_dict(dict(reversed(list(case_payload.items())))).bundle_id
    changed = copy.deepcopy(case_payload)
    changed["parameter_version"] = "synthetic_parameters_2"
    assert initial.bundle_id != CaseBundle.from_dict(changed).bundle_id


def test_artifact_tamper_and_source_tamper_are_detected(case_payload):
    bundle = CaseBundle.from_dict(case_payload)
    assert bundle.verify_artifacts(check_sources=True)["passed"]
    from pathlib import Path
    Path(case_payload["artifacts"][0]["path"]).write_text("tampered", encoding="utf-8")
    assert not bundle.verify_artifacts()["passed"]
    assert not ready_report(bundle)["artifact_integrity_passed"]


def test_source_tamper_is_checked_even_when_artifacts_unchanged(case_payload):
    bundle = CaseBundle.from_dict(case_payload)
    from pathlib import Path
    Path(next(iter(case_payload["source_hashes"]))).write_text("tampered source", encoding="utf-8")
    assert bundle.verify_artifacts()["passed"]
    assert not bundle.verify_artifacts(check_sources=True)["passed"]


def test_roundtrip_and_exclusive_write(case_payload, tmp_path):
    bundle = CaseBundle.from_dict(case_payload)
    path = tmp_path / "case_bundle.json"
    bundle.write(path)
    assert CaseBundle.read(path).bundle_id == bundle.bundle_id
    assert json.loads(path.read_text(encoding="utf-8")) == bundle.to_dict()
    with pytest.raises(FileExistsError):
        bundle.write(path)


@pytest.mark.parametrize("field,value", [
    ("interface_version", "old"), ("artifacts", []), ("source_hashes", {}),
    ("units", {}), ("capabilities_required", ["tes", "tes"]),
])
def test_invalid_case_contract(case_payload, field, value):
    case_payload[field] = value
    with pytest.raises(BundleValidationError):
        CaseBundle.from_dict(case_payload)


@pytest.mark.parametrize("value", ["False", "true", 0, 1, None])
def test_case_status_bool_is_strict(case_payload, value):
    case_payload["status"]["input_valid"] = value
    with pytest.raises(BundleValidationError):
        CaseBundle.from_dict(case_payload)


@pytest.mark.parametrize("field,value", [
    ("mode", "mixed"), ("objective", "weighted"), ("epsilon_carbon_kg", -1),
    ("epsilon_carbon_kg", float("inf")), ("epsilon_carbon_kg", float("nan")),
    ("epsilon_carbon_kg", True), ("tes_enabled", "False"),
])
def test_solve_request_rejects_illegal_fields(case_payload, field, value):
    request = solve_payload(case_payload)
    request[field] = value
    with pytest.raises(BundleValidationError):
        SolveRequest.from_dict(request)


@pytest.mark.parametrize("field,value", [("threads", 0), ("threads", True), ("random_seed", -1),
                                          ("mip_gap", 2), ("mip_gap", True), ("time_limit_s", 0)])
def test_invalid_solver_settings(case_payload, field, value):
    request = solve_payload(case_payload)
    request["solver"][field] = value
    with pytest.raises(BundleValidationError):
        SolveRequest.from_dict(request)


def test_solve_request_valid_and_cost_only_epsilon(case_payload):
    request = solve_payload(case_payload)
    assert SolveRequest.from_dict(request).payload["mode"] == "hybrid"
    request["objective"] = "carbon"
    with pytest.raises(BundleValidationError):
        SolveRequest.from_dict(request)
    request["epsilon_carbon_kg"] = None
    assert SolveRequest.from_dict(request).payload["objective"] == "carbon"


def test_implemented_flag_cannot_enable_model_and_gate_does_not_import_model(case_payload, monkeypatch):
    import builtins
    original_import = builtins.__import__

    def forbid_model_import(name, *args, **kwargs):
        if name.startswith(("urbanheatopt.model", "pyomo", "highspy", "legacy")):
            raise AssertionError("readiness must not instantiate/import a model or solver")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", forbid_model_import)
    case_payload["implemented"] = True
    case_payload["model_ready"] = True
    report = ready_report(CaseBundle.from_dict(case_payload))
    assert report["snapshot_complete"]
    assert report["artifact_integrity_passed"]
    assert not report["model_ready"]
    assert not report["solver_executed"]
    assert report["registered_model_adapter"] is None
    assert any(item["id"] == "new_case_consumer" for item in report["blockers"])


def result_payload(case_payload):
    return {
        "interface_version": INTERFACE_VERSION,
        "case_bundle_id": CaseBundle.from_dict(case_payload).bundle_id,
        "solve_request_id": SolveRequest.from_dict(solve_payload(case_payload)).bundle_id,
        "run_id": "synthetic_contract_test_only",
        "artifacts": [], "solver_executed": False,
        "termination_condition": "not_executed", "qualified": False,
    }


@pytest.mark.parametrize("termination", ["infeasible", "unbounded", "error", "interrupted", "time_limit", "not_executed"])
def test_failed_or_unexecuted_result_cannot_be_qualified(case_payload, termination):
    result = result_payload(case_payload)
    result.update(termination_condition=termination, qualified=True, solver_executed=termination != "not_executed")
    with pytest.raises(BundleValidationError):
        ResultBundle.from_dict(result)


def test_result_requires_log_qa_and_bound_evidence(case_payload, tmp_path):
    result = result_payload(case_payload)
    assert not ResultBundle.from_dict(result).payload["qualified"]
    result.update(solver_executed=True, termination_condition="optimal", qualified=True)
    with pytest.raises(BundleValidationError):
        ResultBundle.from_dict(result)
    result.update(solve_evidence={"incumbent": 100, "best_bound": 100, "certified_gap": 0, "accepted_gap": 0.01},
                  qa={"passed": True})
    with pytest.raises(BundleValidationError):
        ResultBundle.from_dict(result)
    for role in ("solver_log", "independent_qa"):
        path = tmp_path / f"{role}.json"
        path.write_text('{"synthetic_contract_test_only":true}', encoding="utf-8")
        result["artifacts"].append({"role": role, "path": str(path), "sha256": sha256_file(path)})
    valid = ResultBundle.from_dict(result)
    assert valid.verify_artifacts()["passed"]
    result["solve_evidence"]["best_bound"] = 50
    with pytest.raises(BundleValidationError, match="重新计算不一致"):
        ResultBundle.from_dict(result)
    result["solve_evidence"]["best_bound"] = 100
    result["solve_evidence"]["certified_gap"] = 0.1
    with pytest.raises(BundleValidationError):
        ResultBundle.from_dict(result)


def test_skipped_integrity_check_does_not_claim_verified(case_payload):
    report = ready_report(CaseBundle.from_dict(case_payload), verify_files=False)
    assert not report["artifact_integrity_passed"]
    assert not report["model_ready"]
