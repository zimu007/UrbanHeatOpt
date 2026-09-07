"""A integration interface tests, NOT 62-building or full-season validation.

Source/adapter results below explicitly declare synthetic metadata. Tiny files
exercise hand-off plumbing only; no real delivery, desktop or solver is used.
"""

from __future__ import annotations

import builtins
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

from urbanheatopt import cli
from urbanheatopt.data import integration
from urbanheatopt.data.bundles import CaseBundle, sha256_file
from urbanheatopt.data.gap_catalog import render_gap_report


@pytest.fixture
def synthetic_pipeline(tmp_path, monkeypatch):
    """Stub both upstream validators; this does not validate real hourly data."""
    repository = tmp_path / "repository"
    repository.mkdir()
    delivery_scope = tmp_path / "IN_DATA" / "v0.2"
    delivery = delivery_scope / "synthetic_delivery"
    delivery.mkdir(parents=True)
    source_path = delivery / "synthetic_source.txt"
    source_path.write_text("synthetic interface test only", encoding="utf-8")
    roots = SimpleNamespace(scope_root=delivery_scope.resolve(), requested_root=delivery_scope.resolve(),
                            delivery_root=delivery.resolve(), equipment_patch_root=None)
    config = dict(config_version="guanggu_v2_a_1.0.0", delivery_root=str(delivery_scope),
                  source_profile="guanggu_v03", economic_package="revised_20260831",
                  economic_scenario="revised_base", v2_parameter_scenario="v2_primary_expansion_check",
                  source_permission_policy="require_consistent",
                  output_root=str(repository / "work" / "case"), scope="heating-season",
                  full_audit=True, mode_scope=["central", "distributed", "hybrid"],
                  tes_enabled=False, spatial_inputs={}, desktop_gap_report=False)
    config_path = repository / "synthetic_config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    calls = []

    class FakeSource:
        valid = True
        data_version = "SYNTHETIC_INTERFACE_TEST_ONLY"
        issues = []

        def to_dict(self):
            return {"valid": self.valid, "data_version": self.data_version,
                    "evidence_scope": "stubbed metadata, not full-season audit", "issues": []}

    def validator(root, **kwargs):
        calls.append(("source", root, kwargs))
        return FakeSource()

    def adapter(root, output, **kwargs):
        calls.append(("adapter", root, kwargs))
        output.mkdir(parents=True)
        files = {}
        for key in ("buildings_path", "archetype_map_path", "loads_path", "equipment_performance_path", "timestamp_hour_map_path"):
            path = output / f"{key}.json"
            path.write_text('{"synthetic_interface_test_only":true}', encoding="utf-8")
            files[key] = path
        data = SimpleNamespace(
            building_count=62, load_row_count=133920,
            external_timeseries=pd.DataFrame({"synthetic_interface_test_only": [True]}),
        )
        return SimpleNamespace(canonical_data=data, canonical_report=SimpleNamespace(valid=True), **files)

    snapshot = dict(snapshot_id="synthetic_parameter_snapshot", parameter_valid=True,
                    registry={"synthetic": {"parameter_id": "synthetic", "value": 3}},
                    effective={"station_cost_boundary": "teacher_confirmed_v2_scenario",
                               "station_cost_scenario": "base", "station_capex_CNY": 3_000_000},
                    v2_freeze_patch={"program_feasibility_scope": False,
                                     "economic_conclusion_scope": True,
                                     "allowed_for_primary_economic_conclusion": True}, pending=[])

    def package_reader(root, scenario, **kwargs):
        calls.append(("parameters", root, scenario))
        return copy.deepcopy(snapshot)

    def external_adapter(frame, active_snapshot):
        assert active_snapshot["snapshot_id"] == snapshot["snapshot_id"]
        return frame.copy(deep=True)

    def capacity_handoff(adapted, active_snapshot, data_version, output):
        candidate = output / "candidate_sites.geojson"
        capacity = output / "capacity_boundaries.json"
        candidate.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        capacity.write_text('{"synthetic_interface_test_only":true}', encoding="utf-8")
        return {
            "candidate_sites_path": candidate,
            "capacity_boundaries_path": capacity,
            "capacity_boundaries": {"synthetic_interface_test_only": True},
            "spatial_status": {"status": "synthetic_interface_test_only"},
        }

    def ab_smoke(bundle, requests, capacity_payload, output):
        evidence = {
            "b1_real_bundle_smoke_pass": True,
            "b2_capacity_adapter_smoke_pass": True,
            "monthly_demand_charge_ready": True,
            "effective_parameter_mapping_ready": True,
            "site_capacity_ready": True,
            "pipe_capacity_ready": True,
            "tes_capacity_and_power_ready": True,
            "result_bundle_contract_ready": True,
            "research_boundary_use_allowed": True,
            "publication_parameters_verified": False,
            "solver_instantiated": False,
        }
        integration.write_json(output / "ab_adapter_smoke.json", evidence)
        return evidence

    monkeypatch.setattr(integration, "REPOSITORY_ROOT", repository)
    monkeypatch.setattr(integration, "resolve_guanggu_v03_source_roots", lambda _: roots)
    monkeypatch.setattr(integration, "validate_guanggu_v03_delivery", validator)
    monkeypatch.setattr(integration, "adapt_guanggu_v03_sources", adapter)
    monkeypatch.setattr(integration, "read_revised_package", package_reader)
    monkeypatch.setattr(integration, "revised_timeseries", external_adapter)
    monkeypatch.setattr(integration, "prepare_research_boundaries", capacity_handoff)
    monkeypatch.setattr(integration, "run_ab_adapter_smoke", ab_smoke)
    monkeypatch.setattr(integration.subprocess, "check_output", lambda *args, **kwargs: "synthetic_git_identity\n")
    return SimpleNamespace(repository=repository, roots=roots, source=source_path, config=config,
                           config_path=config_path, calls=calls, snapshot=snapshot, adapter=adapter)


def save_config(fixture, **changes):
    values = dict(fixture.config)
    values.update(changes)
    fixture.config_path.write_text(yaml.safe_dump(values), encoding="utf-8")
    return fixture.config_path


@pytest.mark.parametrize("changes", [
    {"unknown_field": True}, {"config_version": "old"},
    {"economic_package": "provisional_20260829"}, {"source_profile": "wuhan_v02"},
    {"v2_parameter_scenario": "unknown"},
    {"full_audit": False}, {"source_permission_policy": "ignore"},
    {"mode_scope": ["central"]}, {"spatial_inputs": []},
])
def test_a_config_rejects_unknown_old_and_weakened_profiles(synthetic_pipeline, changes):
    with pytest.raises(ValueError):
        integration.load_config(save_config(synthetic_pipeline, **changes))


@pytest.mark.parametrize("field", ["tes_enabled", "full_audit", "desktop_gap_report"])
@pytest.mark.parametrize("value", ["False", "true", 0, 1, None])
def test_a_configuration_booleans_are_strict(synthetic_pipeline, field, value):
    with pytest.raises(ValueError):
        integration.load_config(save_config(synthetic_pipeline, **{field: value}))


def test_a_output_never_writes_raw_input_or_external_results(synthetic_pipeline):
    f = synthetic_pipeline
    for destination in (f.roots.scope_root, f.repository.parent / "OUT_RESULT"):
        with pytest.raises(ValueError, match="输出必须"):
            integration.run_input_pipeline("prepare", f.config_path, run_id="safe", output_root=destination)
        assert not (destination / "safe").exists()
    assert f.calls == []


def test_a_existing_run_is_not_overwritten(synthetic_pipeline):
    f = synthetic_pipeline
    target = f.repository / "work" / "case" / "existing"
    target.mkdir(parents=True)
    marker = target / "keep.txt"
    marker.write_text("keep previous evidence", encoding="utf-8")
    with pytest.raises(FileExistsError):
        integration.run_input_pipeline("prepare", f.config_path, run_id="existing")
    assert marker.read_text(encoding="utf-8") == "keep previous evidence"
    assert f.calls == []


def test_a_synthetic_prepare_bundle_and_independent_states(synthetic_pipeline):
    f = synthetic_pipeline
    initial_hash = sha256_file(f.source)
    summary = integration.run_input_pipeline("prepare", f.config_path, run_id="synthetic_success")
    output = Path(summary["output_dir"])
    assert summary["exit_code"] == 0
    for key in ("input_valid", "parameter_valid", "canonical_valid", "snapshot_complete", "input_hashes_unchanged"):
        assert summary[key] is True
    assert summary["model_ready"] is True
    assert summary["research_solve_ready"] is True
    assert summary["publication_ready"] is False
    assert summary["solver_executed"] is False
    assert summary["result_qualified"] is False
    assert initial_hash == sha256_file(f.source)
    assert json.loads((output / "input_hashes_before.json").read_text(encoding="utf-8")) == json.loads((output / "input_hashes_after.json").read_text(encoding="utf-8"))
    bundle = CaseBundle.read(output / "case_bundle.json")
    assert bundle.payload["data_version"] == "SYNTHETIC_INTERFACE_TEST_ONLY"
    assert bundle.verify_artifacts(check_sources=True)["passed"]
    assert bundle.payload["physical_scope"] == {"buildings": 62, "hours": 2160, "supply_C": 45, "return_C": 40, "peak_capacity_margin_fraction": 0.20}
    gate = json.loads((output / "model_readiness_report.json").read_text(encoding="utf-8"))
    assert gate["snapshot_complete"] and gate["model_ready"]
    assert gate["research_solve_ready"] and not gate["publication_ready"]
    assert any(item["id"] == "publication_station_cost_boundary" for item in gate["blockers"])
    assert [call[0] for call in f.calls] == ["source", "parameters", "adapter"]
    assert f.calls[0][2]["full_audit"] is True
    assert f.calls[2][2]["full_audit"] is True


def test_a_gap_md_matches_json_and_temp_desktop_keeps_backup(synthetic_pipeline, tmp_path):
    f = synthetic_pipeline
    save_config(f, desktop_gap_report=True)
    desktop = tmp_path / "synthetic_desktop"
    desktop.mkdir()
    report_path = desktop / "缺失数据清单.md"
    report_path.write_text("previous user report, keep it", encoding="utf-8")
    summary = integration.run_input_pipeline("prepare", f.config_path, run_id="synthetic_desktop", desktop_path=report_path)
    output = Path(summary["output_dir"])
    items = json.loads((output / "input_gaps.json").read_text(encoding="utf-8"))
    expected = render_gap_report(items, f.snapshot)
    assert report_path.read_text(encoding="utf-8") == expected
    assert (output / "缺失数据清单.md").read_text(encoding="utf-8") == expected
    for item in items:
        assert f"## {item['gap_id']}：" in expected
    assert Path(summary["desktop_previous_backup"]).read_text(encoding="utf-8") == "previous user report, keep it"


def test_a_parameter_conflict_is_exit2_not_fake_snapshot(synthetic_pipeline, monkeypatch):
    f = synthetic_pipeline

    def conflict(*args, **kwargs):
        raise ValueError("synthetic_parameter: 参数允许但来源禁用（合成冲突）")

    monkeypatch.setattr(integration, "read_revised_package", conflict)
    summary = integration.run_input_pipeline("prepare", f.config_path, run_id="conflict")
    output = Path(summary["output_dir"])
    assert summary["exit_code"] == 2
    assert summary["input_valid"] and summary["canonical_valid"]
    assert not summary["parameter_valid"] and not summary["snapshot_complete"]
    assert not summary["model_ready"] and not summary["solver_executed"]
    assert not (output / "case_bundle.json").exists()
    assert any("来源禁用" in item for item in summary["errors"])


def test_a_validate_does_not_pretend_canonical_prepare(synthetic_pipeline):
    f = synthetic_pipeline
    summary = integration.run_input_pipeline("validate", f.config_path, run_id="validate_only")
    assert summary["exit_code"] == 0
    assert summary["input_valid"] and summary["parameter_valid"]
    assert not summary["canonical_valid"] and not summary["snapshot_complete"]
    assert not any(call[0] == "adapter" for call in f.calls)


def test_a_cli_prepare_calls_new_pipeline(synthetic_pipeline, capsys):
    f = synthetic_pipeline
    assert cli.main(["prepare", "--config", str(f.config_path), "--run-id", "cli_prepare"]) == 0
    assert '"solver_executed": false' in capsys.readouterr().out
    assert (f.repository / "work" / "case" / "cli_prepare" / "case_bundle.json").is_file()


@pytest.mark.parametrize("command", ["solve", "report", "diagnose", "tes-check"])
def test_a_unregistered_cli_never_imports_or_calls_legacy(synthetic_pipeline, monkeypatch, capsys, command):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.startswith(("legacy", "pyomo", "highspy", "urbanheatopt.model", "urbanheatopt.optimization")):
            raise AssertionError(f"forbidden backend import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert cli.main([command, "--config", str(synthetic_pipeline.config_path)]) == 2
    assert "没有旧模型回退" in capsys.readouterr().out
    assert not (synthetic_pipeline.repository / "work").exists()


def test_a_cli_invalid_config_returns2(synthetic_pipeline, capsys):
    path = save_config(synthetic_pipeline, economic_package="old")
    assert cli.main(["prepare", "--config", str(path)]) == 2
    assert "输入/接口错误" in capsys.readouterr().err
