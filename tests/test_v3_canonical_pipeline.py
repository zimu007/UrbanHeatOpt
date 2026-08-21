"""Canonical boundary and new-mainline orchestration tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pandas as pd
import pytest

from competition.canonical import CanonicalCaseData, PipeTypeSpec, StorageSpec
from competition.core_model import EconomicInput, SegmentSpec, TechnologySpec
from competition.pipelines.case_pipeline import PipelineRun, run_case_pipeline
from competition.solvers import SolverSettings
from competition.validation.v3_inputs import V3InputError, load_v3_case
from scripts.run_case import main as run_main


def _technology(role: str) -> TechnologySpec:
    values = {
        "central_hp": ("air_source_heat_pump", "central", "electricity", 4.0, None),
        "central_boiler": ("gas_boiler", "central", "gas", None, 0.9),
        "local_hp": ("air_source_heat_pump", "local", "electricity", 3.5, None),
    }
    kind, scope, carrier, cop, efficiency = values[role]
    return TechnologySpec(
        technology_id=role, technology_type=kind, applicable_scope=scope,
        energy_carrier=carrier, cop=cop, efficiency=efficiency,
        capacity_min_kW=0, capacity_max_kW=20, capex_CNY_per_kW=1,
        fixed_maintenance_fraction_per_year=0,
        variable_om_CNY_per_kWh_th=0, lifetime_years=20,
        source="synthetic_test", assumption_flag="synthetic_test",
    )


def _canonical() -> CanonicalCaseData:
    timestamp = pd.Timestamp("2026-01-01T00:00:00+08:00")
    return CanonicalCaseData(
        contract_version="competition_input_3.0.0-draft.1",
        software_release_track="test_v0",
        case_id="canonical", scenario_id="smoke", data_version="synthetic-v3",
        profile="v0-smoke", modes=("central", "distributed", "hybrid"),
        timestamps=(timestamp,), hours=(1,), site_node="site_1",
        demand_nodes=("building_1",), heat_demand_kW_th={("building_1", 1): 10.0},
        technologies=tuple(_technology(role) for role in ("central_hp", "central_boiler", "local_hp")),
        storage=StorageSpec("tes", 10, 5, 5, 0.95, 0.95, 0.0, 0, 0, 0, 15, "synthetic_test", "v1"),
        segments=(SegmentSpec("s1", "site_1", "building_1", 10, 20, 1, 30),),
        pipe_types=(
            PipeTypeSpec("p1", 1, 10, 1, 0, 0, 30, "synthetic_test", "v1"),
            PipeTypeSpec("p2", 2, 15, 2, 0, 0, 30, "synthetic_test", "v1"),
            PipeTypeSpec("p3", 3, 20, 3, 0, 0, 30, "synthetic_test", "v1"),
        ),
        economics=EconomicInput(
            {1: 1}, {1: 0.5}, {1: 0.3}, 1,
            {"building_1": 0}, {"building_1": 30}, 1_000_000,
        ),
        solver=SolverSettings(mip_gap=0), input_sha256={"case_config.yaml": "0" * 64},
        parameter_versions={"central_hp": "v1"}, raw_config={"marker": [1, 2]},
    )


def test_canonical_case_is_deeply_read_only_and_projects_same_inputs() -> None:
    data = _canonical()
    assert isinstance(data.input_sha256, MappingProxyType)
    assert isinstance(data.raw_config, MappingProxyType)
    with pytest.raises(TypeError):
        data.raw_config["marker"] = ()
    assert data.to_core_input("central").heat_demand_kW == data.to_core_input("hybrid").heat_demand_kW


def test_pipeline_solves_all_modes_without_legacy_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("competition.pipelines.case_pipeline.load_v3_case", lambda *_args, **_kwargs: _canonical())
    monkeypatch.setattr(
        "competition.pipelines.case_pipeline.export_v3_results",
        lambda *_args, **_kwargs: SimpleNamespace(
            pareto_csv=Path("pareto_points.csv"),
            candidate_sites_geojson=Path("generated_candidate_sites.geojson"),
            qa_summary_json=Path("qa_summary.json"),
        ),
    )
    result = run_case_pipeline(tmp_path / "unused", profile="v0-smoke", output_root=tmp_path / "runs")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    modes = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert manifest["legacy_model_used"] is False
    assert [row["mode"] for row in modes] == ["central", "distributed", "hybrid"]
    assert all(row["termination_condition"] == "optimal" for row in modes)


def test_public_command_only_delegates_to_new_pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manifest = tmp_path / "run_manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    observed: dict[str, object] = {}

    def fake(case: str, *, profile: str, output_root: str) -> PipelineRun:
        observed.update(case=case, profile=profile, output_root=output_root)
        return PipelineRun(tmp_path, manifest, tmp_path / "summary.json", {})

    monkeypatch.setattr("scripts.run_case.run_case_pipeline", fake)
    monkeypatch.setattr("sys.argv", ["run_case.py", "--case", "case", "--profile", "v0-smoke", "--output-root", "out"])
    assert run_main() == 0
    assert observed == {"case": "case", "profile": "v0-smoke", "output_root": "out"}


def test_v3_loader_rejects_legacy_contract_before_file_adaptation(tmp_path: Path) -> None:
    (tmp_path / "case_config.yaml").write_text("contract_version: competition_input_v2_1\n", encoding="utf-8")
    with pytest.raises(V3InputError) as captured:
        load_v3_case(tmp_path)
    assert "3.0.0-draft.1" in str(captured.value)
