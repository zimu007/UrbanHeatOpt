"""Canonical boundary and new-mainline orchestration tests."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from types import MappingProxyType, SimpleNamespace

import pandas as pd
import geopandas as gpd
import pytest
import yaml
from shapely.geometry import LineString, Point

from competition.canonical import CanonicalCaseData, PipeTypeSpec, StorageSpec
from competition.core_model import EconomicInput, SegmentSpec, TechnologySpec
from competition.physical_interfaces import FixedV0PerformanceProvider
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
    technologies = tuple(
        _technology(role) for role in ("central_hp", "central_boiler", "local_hp")
    )
    performance = FixedV0PerformanceProvider().precompute(
        technologies=technologies,
        hours=(1,),
        timestamps=(timestamp,),
        outdoor_temperature_C=(0.0,),
        leaving_water_temperature_C=50.0,
    )
    return CanonicalCaseData(
        contract_version="competition_input_3.0.0-draft.2",
        software_release_track="test_v0",
        case_id="canonical", scenario_id="smoke", data_version="synthetic-v3",
        profile="v0-smoke", modes=("central", "distributed", "hybrid"),
        timestamps=(timestamp,), hours=(1,), site_node="site_1",
        demand_nodes=("building_1",), heat_demand_kW_th={("building_1", 1): 10.0},
        technologies=technologies,
        storage=StorageSpec("tes", 10, 5, 5, 0.95, 0.95, 0.0, 0, 0, 0, 15, "synthetic_test", "v1"),
        segments=(SegmentSpec("s1", "site_1", "building_1", 10, 20, 1, 30),),
        pipe_types=(
            PipeTypeSpec("p1", 1, 10, 1, 0, 0, 30, "synthetic_test", "v1"),
            PipeTypeSpec("p2", 2, 15, 2, 0, 0, 30, "synthetic_test", "v1"),
            PipeTypeSpec("p3", 3, 20, 3, 0, 0, 30, "synthetic_test", "v1"),
        ),
        heat_pump_performance=performance,
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


def test_delivery_command_delegates_to_wuhan_new_core_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed = {}
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    def fake(delivery_root, **kwargs):
        observed.update({"delivery_root": delivery_root, **kwargs})
        return PipelineRun(tmp_path, manifest, tmp_path / "summary.json", {})

    monkeypatch.setattr("scripts.run_case.run_wuhan_v02_pipeline", fake)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_case.py", "--delivery-root", "delivery", "--source-profile", "wuhan_v02",
            "--assumption-profile", "provisional_v0", "--profile", "v0-smoke",
            "--output-root", "out",
        ],
    )
    assert run_main() == 0
    assert observed == {
        "delivery_root": "delivery",
        "source_profile": "wuhan_v02",
        "assumption_profile": "provisional_v0",
        "profile": "v0-smoke",
        "output_root": "out",
    }


def test_v3_loader_rejects_legacy_contract_before_file_adaptation(tmp_path: Path) -> None:
    (tmp_path / "case_config.yaml").write_text("contract_version: competition_input_v2_1\n", encoding="utf-8")
    with pytest.raises(V3InputError) as captured:
        load_v3_case(tmp_path)
    assert "3.0.0-draft.2" in str(captured.value)


def _copy_two_station_fixture(tmp_path: Path) -> Path:
    source = Path(__file__).parent / "fixtures" / "v3_smoke_case"
    case_dir = tmp_path / "two_station_case"
    shutil.copytree(source, case_dir)
    sites = gpd.GeoDataFrame(
        {
            "site_id": ["station_A", "station_B"],
            "candidate_rank": [1, 2],
            "data_version": ["synthetic-v3-draft1"] * 2,
        },
        geometry=[Point(114.3006, 30.5006), Point(114.3016, 30.5006)],
        crs="EPSG:4326",
    )
    sites.to_file(case_dir / "candidate_sites.geojson", driver="GeoJSON")
    network = gpd.GeoDataFrame(
        {
            "segment_id": ["segment_A", "segment_B", "segment_link"],
            "node_from": ["station_A", "station_B", "building_1"],
            "node_to": ["building_1", "building_2", "building_2"],
            "length_m": [80.0, 90.0, 70.0],
            "data_version": ["synthetic-v3-draft1"] * 3,
        },
        geometry=[
            LineString([(114.3006, 30.5006), (114.3001, 30.5001)]),
            LineString([(114.3016, 30.5006), (114.3011, 30.5001)]),
            LineString([(114.3001, 30.5001), (114.3011, 30.5001)]),
        ],
        crs="EPSG:4326",
    )
    network.to_file(case_dir / "candidate_network.geojson", driver="GeoJSON")
    return case_dir


def test_v3_loader_accepts_multiple_candidate_stations_without_selecting_one(
    tmp_path: Path,
) -> None:
    case = load_v3_case(_copy_two_station_fixture(tmp_path), profile="v0-smoke")
    assert case.candidate_station_nodes == ("station_A", "station_B")
    assert case.site_node is None
    assert case.max_built_stations == 1
    core = case.to_core_input("hybrid")
    assert core.candidate_station_nodes == case.candidate_station_nodes
    assert core.site_node is None


def test_v3_loader_rejects_duplicate_and_building_station_ids(tmp_path: Path) -> None:
    case_dir = _copy_two_station_fixture(tmp_path)
    sites = gpd.read_file(case_dir / "candidate_sites.geojson")
    sites.loc[1, "site_id"] = "station_A"
    sites.to_file(case_dir / "candidate_sites.geojson", driver="GeoJSON")
    with pytest.raises(V3InputError, match="unique"):
        load_v3_case(case_dir, profile="v0-smoke")

    sites.loc[1, "site_id"] = "building_1"
    sites.to_file(case_dir / "candidate_sites.geojson", driver="GeoJSON")
    with pytest.raises(V3InputError, match="overlap"):
        load_v3_case(case_dir, profile="v0-smoke")


def test_v3_loader_rejects_max_built_sites_above_one(tmp_path: Path) -> None:
    case_dir = _copy_two_station_fixture(tmp_path)
    config_path = case_dir / "case_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["spatial"]["max_built_sites"] = 2
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(
        V3InputError, match="MULTI_STATION_OPERATION_OUT_OF_SCOPE_FOR_V1"
    ):
        load_v3_case(case_dir, profile="v0-smoke")


def test_v1_full_never_falls_back_to_fixed_v0_performance(tmp_path: Path) -> None:
    source = Path(__file__).parent / "fixtures" / "v3_smoke_case"
    case_dir = tmp_path / "v1_case"
    shutil.copytree(source, case_dir)
    config_path = case_dir / "case_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["run"]["profile"] = "v1-full"
    config["time"]["complete_heating_season"] = True
    config["features"].update(
        temperature_cop_enabled=True,
        pipe_loss_enabled=True,
        pumping_enabled=True,
    )
    config["network"].update(
        loss_model="linear_per_m",
        pumping_model="linear_per_kWh_transferred",
    )
    config["performance"].update(
        cop_model="temperature_interpolated",
        capacity_derating_model="temperature_interpolated",
    )
    config["planning"]["unserved_policy"] = "forbidden_for_v1"
    config["pareto"]["point_count"] = 11
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(V3InputError, match="不得使用 V0 固定 COP"):
        load_v3_case(
            case_dir,
            profile="v1-full",
            performance_provider=FixedV0PerformanceProvider(),
        )
