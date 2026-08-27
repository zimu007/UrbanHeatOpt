"""competition_input 3.0.0 draft contract tests."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
import yaml


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "competition" / "schemas" / "case_config_v3.schema.json"
MACHINE_CONTRACT_PATH = ROOT / "competition" / "schemas" / "input_contract_v3.yaml"


def _config(profile: str = "v0-smoke") -> dict[str, object]:
    is_full = profile == "v1-full"
    return {
        "contract_version": (
            "competition_input_3.0.0" if is_full else "competition_input_3.0.0-draft.2"
        ),
        "software_release_track": "formal_v1" if is_full else "test_v0",
        "case_id": "minimal_v3",
        "scenario_id": "smoke",
        "data_version": "synthetic-v3-draft1",
        "data_classification": "synthetic_test",
        "time": {
            "start": "2026-01-01T00:00:00+08:00",
            "end": "2026-04-01T00:00:00+08:00" if is_full else "2026-01-02T00:00:00+08:00",
            "timezone": "Asia/Shanghai",
            "frequency": "1h",
            "interval": "start_inclusive_end_exclusive",
            "complete_heating_season": is_full,
        },
        "units": {
            "heating_power": "kW_th", "heating_energy": "kWh_th",
            "electric_power": "kW_e", "electric_energy": "kWh_e",
            "gas_energy": "kWh_LHV", "storage_energy": "kWh_th",
            "currency": "CNY", "annual_cost": "CNY_per_year",
            "carbon": "kgCO2e_per_year", "electricity_price": "CNY_per_kWh_e",
            "gas_price": "CNY_per_kWh_LHV",
            "electricity_carbon_intensity": "kgCO2e_per_kWh_e",
            "gas_carbon_intensity": "kgCO2e_per_kWh_LHV",
            "time_weight": "h_per_year", "length": "m", "temperature": "degC",
        },
        "crs": {"input": "EPSG:4326", "projected": "EPSG:32650"},
        "files": {
            "buildings": "buildings.geojson",
            "building_archetype_map": "building_archetype_map.csv",
            "building_hourly_loads": "building_hourly_loads.parquet",
            "technologies": "technologies.csv",
            "roads_or_feasible_space": "roads_or_feasible_space.geojson",
            "external_timeseries": "external_timeseries.parquet",
            "pipe_types": "pipe_types.csv",
            "candidate_sites": "candidate_sites.geojson",
            "candidate_network": "candidate_network.geojson",
        },
        "run": {"profile": profile, "modes": ["central", "distributed", "hybrid"]},
        "spatial": {
            "input_mode": "roads", "candidate_source": "provided",
            "candidate_site_count_min": 1, "candidate_site_count_max": 10,
            "max_built_sites": 1,
        },
        "demand": {
            "area_scaling_already_applied": True,
            "includes_dhw": False,
            "includes_cooling": False,
            "ventilation_system": "dedicated_fresh_air",
            "fresh_air_load_included": True,
        },
        "features": {
            "storage_enabled": True, "temperature_cop_enabled": is_full,
            "pipe_loss_enabled": is_full, "pumping_enabled": is_full,
            "waste_heat_enabled": False,
        },
        "network": {
            "supply_temperature_C": 50, "return_temperature_C": 40,
            "pipe_level_count": 3,
            "loss_model": "linear_per_m" if is_full else "disabled_for_v0",
            "pumping_model": "linear_per_kWh_transferred" if is_full else "disabled_for_v0",
        },
        "planning": {
            "discount_rate": 0.05, "price_base_year": 2026, "currency": "CNY",
            "unserved_policy": "forbidden_for_v1" if is_full else "penalized_for_v0",
            "peak_capacity_margin_fraction": 0.2,
            "carbon_price_scenarios_CNY_per_tCO2e": [0, 50, 100, 150],
        },
        "economics": {
            "annualization_method": "capital_recovery_factor",
            "station_fixed_capex_CNY": 0,
            "station_lifetime_years": 30,
            "connection_capex_CNY_per_demand_node": 0,
            "connection_lifetime_years": 30,
            "hns_penalty_CNY_per_kWh_th": 1000000,
        },
        "performance": {
            "cop_model": "temperature_interpolated" if is_full else "fixed_for_v0",
            "capacity_derating_model": "temperature_interpolated" if is_full else "disabled_for_v0",
            "precompute_coefficients": True,
        },
        "pareto": {
            "method": "epsilon_constraint", "point_count": 11 if is_full else 5,
            "second_objective": "annual_operating_physical_carbon",
            "knee_method": "normalized_max_distance_to_endpoint_chord",
            "topsis_enabled": False,
        },
        "enabled_technology_ids": ["central_hp", "central_boiler", "local_hp", "central_tes"],
        "solver": {
            "name": "highs", "threads": 1, "time_limit_seconds": 60,
            "mip_gap": 0, "random_seed": 202611, "load_solution_only_if_optimal": True,
        },
        "qa": {
            "balance_tolerance_kW": 1e-6, "unserved_tolerance_kWh": 1e-6,
            "cost_tolerance_CNY_per_year": 1e-6,
            "carbon_tolerance_kgCO2e_per_year": 1e-6,
            "deterministic_tolerance": 1e-9,
        },
    }


def _errors(config: dict[str, object]) -> list[object]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(config))


def test_v3_schema_and_machine_contract_are_parseable() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    contract = yaml.safe_load(MACHINE_CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["contract_version"] == "competition_input_3.0.0-draft.2"
    assert contract["version_policy"]["automatic_migration"] == "prohibited"


def test_v0_and_v1_profiles_have_valid_frozen_shapes() -> None:
    assert _errors(_config("v0-smoke")) == []
    assert _errors(_config("v1-full")) == []


def test_old_contract_and_topsis_are_rejected() -> None:
    config = _config()
    config["contract_version"] = "competition_input_v2_1"
    assert _errors(config)
    config = _config()
    config["pareto"]["topsis_enabled"] = True
    assert _errors(config)


def test_v1_requires_full_physics_and_eleven_pareto_points() -> None:
    config = _config("v1-full")
    config["features"]["pipe_loss_enabled"] = False
    config["pareto"]["point_count"] = 5
    messages = " ".join(error.message for error in _errors(config))
    assert "True was expected" in messages
    assert "11 was expected" in messages


def test_provided_candidates_require_both_files() -> None:
    config = _config()
    config["files"]["candidate_network"] = None
    assert _errors(config)
