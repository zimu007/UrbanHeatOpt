from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
import yaml
from shapely.geometry import LineString, Polygon

from competition.adapters.legacy_case import adapt_case_to_legacy, adapt_case_to_legacy_case_directory
from competition.results.standard import export_standard_results
from scripts.create_minimal_case import create_minimal_case
from scripts.run_case import main as run_case_main
from competition.validation.inputs import InputValidationError, validate_case_inputs

pytest.importorskip("pyarrow")


def _write_minimal_case(case_dir: Path) -> None:
    timestamps = pd.date_range(
        "2026-01-01T00:00:00",
        periods=24,
        freq="h",
        tz="Asia/Shanghai",
    )
    buildings = gpd.GeoDataFrame(
        {
            "building_id": [f"building_{idx}" for idx in range(4)],
            "use_type": ["office", "office", "school", "retail"],
            "heated_area_m2": [100.0, 120.0, 140.0, 160.0],
            "archetype_id": ["a", "a", "b", "c"],
            "geometry": [
                Polygon([(114.0, 30.0), (114.001, 30.0), (114.001, 30.001), (114.0, 30.001)]),
                Polygon([(114.002, 30.0), (114.003, 30.0), (114.003, 30.001), (114.002, 30.001)]),
                Polygon([(114.0, 30.002), (114.001, 30.002), (114.001, 30.003), (114.0, 30.003)]),
                Polygon([(114.002, 30.002), (114.003, 30.002), (114.003, 30.003), (114.002, 30.003)]),
            ],
        },
        crs="EPSG:4326",
    )
    buildings.to_file(case_dir / "buildings.geojson", driver="GeoJSON")

    load_rows = []
    for hour_index, timestamp in enumerate(timestamps):
        for building_index in range(4):
            load_rows.append(
                {
                    "timestamp": timestamp,
                    "building_id": f"building_{building_index}",
                    "heating_kW": 5.0 + building_index + hour_index / 100.0,
                    "cooling_kW": 0.0,
                    "dhw_included": False,
                    "data_version": "synthetic-v1",
                    "quality_flag": "synthetic_test",
                }
            )
    pd.DataFrame(load_rows).to_parquet(case_dir / "building_hourly_loads.parquet", index=False)

    technologies = pd.DataFrame(
        [
            {
                "technology_id": "synthetic_fixed_source",
                "technology_type": "fixed_heat_source",
                "applicable_scope": "central",
                "energy_carrier": "synthetic_heat",
                "cop": pd.NA,
                "efficiency": pd.NA,
                "capacity_min_kW": 0.0,
                "capacity_max_kW": 100.0,
                "capex_CNY_per_kW": 10.0,
                "capex_basis": "one_time_capex",
                "fixed_om_CNY_per_kW_year": 0.0,
                "variable_om_CNY_per_kWh_heat": 0.2,
                "lifetime_years": 20,
                "source": "synthetic fixture",
                "assumption_flag": "synthetic_test",
            }
        ]
    )
    technologies.to_csv(case_dir / "technologies.csv", index=False, encoding="utf-8-sig")

    roads = gpd.GeoDataFrame(
        {
            "feature_id": ["road_1"],
            "spatial_role": ["road"],
            "geometry": [LineString([(113.999, 29.999), (114.004, 30.004)])],
        },
        crs="EPSG:4326",
    )
    roads.to_file(case_dir / "roads_or_feasible_space.geojson", driver="GeoJSON")

    external = pd.DataFrame(
        {
            "timestamp": timestamps,
            "data_version": ["synthetic-v1"] * len(timestamps),
        }
    )
    external.to_parquet(case_dir / "external_timeseries.parquet", index=False)

    config = {
        "contract_version": "competition_input_v1",
        "case_id": "minimal",
        "scenario_id": "smoke",
        "data_version": "synthetic-v1",
        "data_classification": "synthetic_test",
        "time": {
            "start": "2026-01-01T00:00:00+08:00",
            "end": "2026-01-02T00:00:00+08:00",
            "timezone": "Asia/Shanghai",
            "frequency": "1h",
            "interval": "start_inclusive_end_exclusive",
            "complete_heating_season": False,
        },
        "units": {
            "heating_power": "kW",
            "heating_energy": "kWh",
            "currency": "CNY",
            "area": "m2",
            "length": "m",
            "temperature": "degC",
            "carbon": "kgCO2e",
        },
        "crs": {"input": "EPSG:4326", "projected": "EPSG:32650"},
        "files": {
            "buildings": "buildings.geojson",
            "building_hourly_loads": "building_hourly_loads.parquet",
            "technologies": "technologies.csv",
            "roads_or_feasible_space": "roads_or_feasible_space.geojson",
            "resource_anchors": None,
            "external_timeseries": "external_timeseries.parquet",
        },
        "clustering": {
            "algorithm": "kmeans",
            "cluster_count": 2,
            "random_seed": 202611,
            "n_init": 10,
        },
        "spatial": {
            "input_mode": "roads",
            "candidate_site_rule": "cluster_centroid_nearest_feature",
            "candidate_network_rule": "delaunay_mst",
            "feasibility_tolerance_m": 0.0,
        },
        "demand": {"area_scaling_already_applied": True},
        "dhw": {"input_includes_dhw": False, "add_in_adapter": False},
        "features": {"waste_heat_enabled": False, "storage_enabled": False},
        "network": {"supply_temperature_C": 55.0, "return_temperature_C": 35.0},
        "planning": {
            "horizon_years": 20,
            "discount_rate": 0.05,
            "price_base_year": 2026,
            "currency": "CNY",
        },
        "enabled_technology_ids": ["synthetic_fixed_source"],
        "solver": {
            "name": "highs",
            "threads": 1,
            "time_limit_seconds": 60,
            "mip_gap": 0.0,
            "load_solution_only_if_optimal": True,
        },
        "qa": {
            "cluster_energy_relative_tolerance": 0.001,
            "balance_tolerance_kW": 0.000001,
            "unserved_tolerance_kWh": 0.000001,
            "cost_tolerance_CNY": 0.000001,
            "deterministic_tolerance": 1e-9,
        },
    }
    (case_dir / "case_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")


@pytest.fixture()
def minimal_case(tmp_path: Path) -> Path:
    case_dir = tmp_path / "minimal_case"
    case_dir.mkdir()
    _write_minimal_case(case_dir)
    return case_dir


def test_complete_input_validator_accepts_minimal_case(minimal_case: Path) -> None:
    inputs = validate_case_inputs(minimal_case)

    assert len(inputs.buildings) == 4
    assert len(inputs.timestamp_hour_map) == 24
    assert inputs.legacy_loads_kW.columns.tolist() == [
        "hour",
        "building_0",
        "building_1",
        "building_2",
        "building_3",
    ]


def test_validator_rejects_building_load_id_mismatch(minimal_case: Path) -> None:
    loads = pd.read_parquet(minimal_case / "building_hourly_loads.parquet")
    loads.loc[0, "building_id"] = "unknown_building"
    loads = loads.sort_values(["timestamp", "building_id"], kind="mergesort").reset_index(drop=True)
    loads.to_parquet(minimal_case / "building_hourly_loads.parquet", index=False)

    with pytest.raises(InputValidationError) as exc:
        validate_case_inputs(minimal_case)
    assert "不存在" in str(exc.value) or "缺少建筑" in str(exc.value) or "完全相同" in str(exc.value)


def test_validator_rejects_unsupported_p0_technology(minimal_case: Path) -> None:
    technologies = pd.read_csv(minimal_case / "technologies.csv")
    technologies.loc[0, "technology_type"] = "air_source_heat_pump"
    technologies.loc[0, "energy_carrier"] = "electricity"
    technologies.loc[0, "cop"] = 3.0
    technologies.to_csv(minimal_case / "technologies.csv", index=False, encoding="utf-8-sig")

    with pytest.raises(InputValidationError) as exc:
        validate_case_inputs(minimal_case)
    assert "P0 当前只可执行 fixed_heat_source" in str(exc.value)


def test_legacy_adapter_writes_expected_files(minimal_case: Path, tmp_path: Path) -> None:
    output = tmp_path / "legacy"
    result = adapt_case_to_legacy(minimal_case, output)

    assert result.building_ts_csv.is_file()
    assert result.building_data_geojson.is_file()
    assert result.timestamp_hour_map_csv.is_file()
    assert result.heat_generation_units_xlsx.is_file()
    assert result.parameter_costs_xlsx.is_file()
    assert result.waste_heat_profiles_xlsx.is_file()

    building_ts = pd.read_csv(result.building_ts_csv)
    building_data = gpd.read_file(result.building_data_geojson)
    assert building_ts.shape == (24, 5)
    assert set(building_data["building_id"]) == {f"building_{idx}" for idx in range(4)}
    assert building_data["YearlyDemand"].sum() == pytest.approx(building_ts.iloc[:, 1:].sum().sum())


def test_run_case_command_reaches_clustering(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    case_dir = create_minimal_case(tmp_path / "case")
    legacy_root = tmp_path / "legacy_cases"
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_case.py",
            "--case",
            str(case_dir),
            "--legacy-root",
            str(legacy_root),
            "--skip-model",
        ],
    )

    assert run_case_main() == 0
    assert (legacy_root / "minimal" / "scenarios" / "smoke" / "data" / "heat_network" / "Heat_Demand.csv").is_file()
    assert (legacy_root / "minimal" / "run_summary.json").is_file()


def test_standard_result_export_writes_contract_outputs(minimal_case: Path, tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy_cases"
    result = adapt_case_to_legacy_case_directory(minimal_case, legacy_root)
    legacy_case = result.output_dir
    expost = legacy_case / "scenarios" / "smoke" / "expost"
    heat_network = legacy_case / "scenarios" / "smoke" / "data" / "heat_network"
    expost.mkdir(parents=True)
    heat_network.mkdir(parents=True)

    loads = validate_case_inputs(minimal_case).legacy_loads_kW
    hourly_total = loads.drop(columns=["hour"]).sum(axis=1)
    pd.DataFrame({"Node": ["heat_node_0"], "DH Connection": [1]}).to_csv(
        expost / "DH_Connection.csv", sep=";", index=False
    )
    pd.DataFrame({"Unit": ["unit_1"], "Investment": [float(hourly_total.max())]}).to_csv(
        expost / "Investment_Decisions.csv", sep=";", index=False
    )
    pd.DataFrame({"From": ["heat_unit_unit_1"], "To": ["heat_node_0"], "Investment": [1]}).to_csv(
        expost / "Pipe_Connections.csv", sep=";", index=False
    )
    dispatch = pd.DataFrame([["unit_1", *hourly_total.tolist()]], columns=["Generation Unit", *range(1, 25)])
    dispatch.to_csv(expost / "Heat_Generation_TS.csv", sep=";", index=False)
    pd.DataFrame({"Costs in kCNY": ["Total Costs", "Central Heat Production Cost"], "Value": [1.0, 1.0]}).to_csv(
        expost / "Economic_Results.csv", sep=";", index=False
    )
    (expost / "solver_report.json").write_text(
        json.dumps(
            {
                "solver": "highs",
                "termination_condition": "optimal",
                "status": "ok",
                "acceptable": True,
                "configured_mip_gap": 0.0,
                "actual_mip_gap": 0.0,
                "runtime_seconds": 0.1,
                "solver_threads": 1,
                "solver_time_limit_seconds": 60,
                "solver_random_seed": 202611,
            }
        ),
        encoding="utf-8",
    )
    pd.DataFrame({"hour": loads["hour"], "heat_node_0": hourly_total}).to_csv(
        heat_network / "Cluster_TS.csv", index=False
    )

    export = export_standard_results(minimal_case, legacy_case)
    qa = json.loads(export.qa_report_json.read_text(encoding="utf-8"))

    assert export.assumptions_yaml.is_file()
    assert export.dispatch_csv.is_file()
    assert export.costs_csv.is_file()
    assert export.solver_report_json.is_file()
    assert export.balance_check_csv.is_file()
    assert export.cost_check_csv.is_file()
    assert qa["acceptable"] is True
    assert qa["unserved_heat_kWh"] == pytest.approx(0.0)
    assert qa["energy_balance_error_kWh"] == pytest.approx(0.0)
    assert qa["max_hourly_balance_error_kW"] == pytest.approx(0.0)
    assert qa["cluster_energy_relative_error"] == pytest.approx(0.0)
    assert qa["cost_reaggregation_error"] == pytest.approx(0.0)
