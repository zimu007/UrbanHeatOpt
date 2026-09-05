"""Resource-level tests for the frozen competition input contract."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from yaml.nodes import MappingNode, Node, SequenceNode


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIRECTORY = REPOSITORY_ROOT / "src" / "urbanheatopt" / "data" / "schemas"


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        assert key not in result, f"duplicate JSON key: {key}"
        result[key] = value
    return result


def _assert_unique_yaml_keys(node: Node, path: str = "$") -> None:
    if isinstance(node, MappingNode):
        seen: set[str] = set()
        for key_node, value_node in node.value:
            key = str(key_node.value)
            assert key not in seen, f"duplicate YAML key at {path}: {key}"
            seen.add(key)
            _assert_unique_yaml_keys(value_node, f"{path}.{key}")
    elif isinstance(node, SequenceNode):
        for index, item in enumerate(node.value):
            _assert_unique_yaml_keys(item, f"{path}[{index}]")


@pytest.fixture(scope="module")
def case_schema() -> dict[str, object]:
    schema_path = SCHEMA_DIRECTORY / "case_config.schema.json"
    schema = json.loads(
        schema_path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_json_object,
    )
    Draft202012Validator.check_schema(schema)
    return schema


@pytest.fixture()
def valid_config() -> dict[str, object]:
    """A schema example only; all numerical values are synthetic test values."""

    return {
        "contract_version": "competition_input_v2_1",
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
            "heating_energy": "kWh_th",
            "electricity_energy": "kWh_e",
            "gas_energy": "kWh_LHV",
            "currency": "CNY",
            "electricity_price": "CNY_per_kWh_e",
            "gas_price": "CNY_per_kWh_LHV",
            "electricity_carbon_intensity": "kgCO2e_per_kWh_e",
            "gas_carbon_intensity": "kgCO2e_per_kWh_LHV",
            "area": "m2",
            "length": "m",
            "temperature": "degC",
            "carbon": "kgCO2e",
            "time_weight": "h_per_year",
            "cop_and_efficiency": "dimensionless",
            "fixed_maintenance_fraction": "fraction_per_year",
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
        "economics": {
            "annualization_method": "simple_capex_divided_by_lifetime_years",
            "expected_weight_sum_h_per_year": 24.0,
            "variable_om_basis": "useful_heat_output_kWh_th",
            "site_capex_policy": "excluded_not_defined_by_approved_formula",
        },
        "network_economics": {
            "pipe_capex_CNY_per_m": 0.0,
            "pipe_lifetime_years": 20,
        },
        "connection_economics": {
            "by_demand_node": {
                "cluster_1": {
                    "connection_capex_CNY": 0.0,
                    "lifetime_years": 20,
                },
                "cluster_2": {
                    "connection_capex_CNY": 0.0,
                    "lifetime_years": 20,
                },
            }
        },
        "reliability": {"hns_penalty_CNY_per_kWh": 1_000_000.0},
        "enabled_technology_ids": [
            "central_ashp",
            "central_gas_boiler",
            "local_ashp",
        ],
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


def test_machine_contract_is_parseable_and_has_no_duplicate_keys() -> None:
    contract_path = SCHEMA_DIRECTORY / "input_contract.yaml"
    raw = contract_path.read_text(encoding="utf-8")
    syntax_tree = yaml.compose(raw)
    assert syntax_tree is not None
    _assert_unique_yaml_keys(syntax_tree)
    contract = yaml.safe_load(raw)

    assert contract["contract_version"] == "competition_input_v2_1"
    assert contract["predecessor_contract"] == {
        "version": "competition_input_v2",
        "status": "deprecated_non_executable_requires_explicit_migration",
        "breaking_changes": [
            "fixed_om_CNY_per_kW_year_replaced_by_fixed_maintenance_fraction_per_year",
            "variable_om_CNY_per_kWh_heat_replaced_by_variable_om_CNY_per_kWh_th",
            "simple_annual_cost_sections_added_to_case_config",
        ],
        "automatic_migration_allowed": False,
    }
    assert contract["canonical_units"]["heating_power"] == "kW"
    external_columns = contract["files"]["external_timeseries"]["required_columns"]
    assert "timestamp" in external_columns
    assert "data_version" in external_columns
    assert contract["legacy_adapter_contract"]["unit_conversion_count"] == 0
    assert contract["technology_support_status"]["contract_executable_types"] == [
        "air_source_heat_pump",
        "gas_boiler",
    ]
    required_roles = contract["p0_execution_scope"]["required_enabled_technology_roles"]
    assert set(required_roles) == {
        "central_air_source_heat_pump",
        "central_gas_boiler",
        "local_air_source_heat_pump",
    }
    external = contract["files"]["external_timeseries"]
    assert "time_weight_h_per_year" in external["required_columns"]
    assert "electricity_price_CNY_per_kWh_e" in external["conditional_columns"]
    assert "electricity_price_CNY_per_kWh" in external["forbidden_price_fields"]
    assert "Heat_Demand.csv" in contract["generated_intermediates"]
    assert "Heat_Network.geojson" in contract["generated_intermediates"]
    assert "NODE_REFERENCE_MISSING" in contract["validation_error_codes"]


def test_valid_synthetic_schema_example_passes(
    case_schema: dict[str, object], valid_config: dict[str, object]
) -> None:
    validator = Draft202012Validator(case_schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(valid_config)) == []


@pytest.mark.parametrize(
    ("path", "invalid_value"),
    [
        (("units", "heating_power"), "W"),
        (("solver", "name"), "appsi_highs"),
        (("clustering", "random_seed"), 1),
        (("dhw", "add_in_adapter"), True),
    ],
)
def test_frozen_contract_values_are_rejected_when_changed(
    case_schema: dict[str, object],
    valid_config: dict[str, object],
    path: tuple[str, str],
    invalid_value: object,
) -> None:
    invalid = copy.deepcopy(valid_config)
    invalid[path[0]][path[1]] = invalid_value
    validator = Draft202012Validator(case_schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(invalid))


def test_unknown_case_config_key_is_rejected(
    case_schema: dict[str, object], valid_config: dict[str, object]
) -> None:
    invalid = copy.deepcopy(valid_config)
    invalid["undocumented_switch"] = True
    validator = Draft202012Validator(case_schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(invalid))


@pytest.mark.parametrize("old_version", ["competition_input_v1", "competition_input_v2"])
def test_old_contract_version_is_rejected(
    case_schema: dict[str, object],
    valid_config: dict[str, object],
    old_version: str,
) -> None:
    invalid = copy.deepcopy(valid_config)
    invalid["contract_version"] = old_version
    validator = Draft202012Validator(case_schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(invalid))


@pytest.mark.parametrize(
    "enabled_ids",
    [
        ["central_ashp"],
        ["central_ashp", "central_gas_boiler"],
        ["central_ashp", "central_ashp", "local_ashp"],
    ],
)
def test_v2_schema_requires_three_unique_enabled_technology_ids(
    case_schema: dict[str, object],
    valid_config: dict[str, object],
    enabled_ids: list[str],
) -> None:
    invalid = copy.deepcopy(valid_config)
    invalid["enabled_technology_ids"] = enabled_ids
    validator = Draft202012Validator(case_schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(invalid))


def test_v2_machine_contract_freezes_independent_device_rows() -> None:
    contract = yaml.safe_load(
        (SCHEMA_DIRECTORY / "input_contract.yaml").read_text(encoding="utf-8")
    )
    technologies = contract["files"]["technologies"]
    row_rules = set(technologies["row_rules"])

    assert "fixed_heat_source" not in technologies["required_columns"][
        "technology_type"
    ]["allowed"]
    assert "synthetic_heat" not in technologies["required_columns"][
        "energy_carrier"
    ]["allowed"]
    assert {
        "air_source_heat_pump_requires_energy_carrier_electricity",
        "air_source_heat_pump_applicable_scope_must_be_central_or_local_not_both",
        "air_source_heat_pump_requires_finite_cop_gt_zero_and_efficiency_null",
        "gas_boiler_requires_energy_carrier_gas",
        "gas_boiler_applicable_scope_must_be_central",
        "gas_boiler_requires_finite_efficiency_gt_zero_lte_one_and_cop_null",
        "v2_enabled_roles_require_distinct_technology_ids",
    } <= row_rules


def test_v2_machine_contract_uses_absolute_prices_and_public_time_weight() -> None:
    contract = yaml.safe_load(
        (SCHEMA_DIRECTORY / "input_contract.yaml").read_text(encoding="utf-8")
    )
    external = contract["files"]["external_timeseries"]
    conditional = external["conditional_columns"]

    assert external["required_columns"]["time_weight_h_per_year"] == {
        "type": "float64",
        "nullable": False,
        "finite": True,
        "exclusive_minimum": 0,
        "unit": "h_per_year",
        "semantics": "public_hour_weight_shared_by_all_technologies",
    }
    assert conditional["electricity_price_CNY_per_kWh_e"]["semantics"] == (
        "absolute_tariff_not_multiplier_or_index"
    )
    assert conditional["gas_price_CNY_per_kWh_LHV"]["semantics"] == (
        "absolute_tariff_not_multiplier_or_index"
    )
    assert conditional["electricity_carbon_kgCO2e_per_kWh_e"]["current_use"] == (
        "input_traceability_only_not_consumed_by_core_cost_objective"
    )
    assert all(
        "multiplier" in field or "index" in field or field.endswith("per_kWh")
        for field in external["forbidden_price_fields"]
    )


def test_v2_machine_contract_freezes_simple_annual_cost_inputs() -> None:
    contract = yaml.safe_load(
        (SCHEMA_DIRECTORY / "input_contract.yaml").read_text(encoding="utf-8")
    )
    columns = contract["files"]["technologies"]["required_columns"]

    assert "fixed_om_CNY_per_kW_year" not in columns
    assert columns["fixed_maintenance_fraction_per_year"] == {
        "type": "float",
        "nullable": False,
        "finite": True,
        "minimum": 0,
        "maximum": 1,
        "unit": "fraction_per_year",
        "semantics": (
            "annual_maintenance_fraction_applied_to_installed_device_capex"
        ),
    }
    assert "variable_om_CNY_per_kWh_heat" not in columns
    assert columns["variable_om_CNY_per_kWh_th"]["unit"] == "CNY_per_kWh_th"


def test_machine_contract_freezes_case_to_core_economic_mapping() -> None:
    contract = yaml.safe_load(
        (SCHEMA_DIRECTORY / "input_contract.yaml").read_text(encoding="utf-8")
    )
    mapping = contract["economic_adapter_contract"]
    assert mapping["case_config_to_core"]["segment_pipe_capex_CNY_per_m"] == (
        "network_economics.pipe_capex_CNY_per_m"
    )
    assert mapping["case_config_to_core"]["connection_capex_CNY_by_node"] == (
        "connection_economics.by_demand_node.*.connection_capex_CNY"
    )
    assert mapping["gas_price_standardization"]["conversion_count"] == 1
    assert mapping["gas_price_standardization"]["formal_default_value"] is None


@pytest.mark.parametrize(
    ("section", "field", "invalid_value"),
    [
        ("economics", "expected_weight_sum_h_per_year", 0.0),
        ("network_economics", "pipe_capex_CNY_per_m", -1.0),
        ("network_economics", "pipe_lifetime_years", 0),
        ("reliability", "hns_penalty_CNY_per_kWh", -1.0),
    ],
)
def test_invalid_simple_annual_cost_config_is_rejected(
    case_schema: dict[str, object],
    valid_config: dict[str, object],
    section: str,
    field: str,
    invalid_value: object,
) -> None:
    invalid = copy.deepcopy(valid_config)
    invalid[section][field] = invalid_value
    validator = Draft202012Validator(case_schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(invalid))


@pytest.mark.parametrize(
    ("path", "invalid_value"),
    [
        (("features", "waste_heat_enabled"), True),
        (("features", "storage_enabled"), True),
        (("files", "resource_anchors"), "resource_anchors.geojson"),
    ],
)
def test_p0_unimplemented_features_are_rejected(
    case_schema: dict[str, object],
    valid_config: dict[str, object],
    path: tuple[str, str],
    invalid_value: object,
) -> None:
    invalid = copy.deepcopy(valid_config)
    invalid[path[0]][path[1]] = invalid_value
    validator = Draft202012Validator(case_schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(invalid))


@pytest.mark.parametrize(
    ("field", "method_identifier"),
    [
        ("candidate_site_rule", "future_site_method"),
        ("candidate_network_rule", "future_network_method"),
    ],
)
def test_spatial_method_identifiers_do_not_freeze_algorithms(
    case_schema: dict[str, object],
    valid_config: dict[str, object],
    field: str,
    method_identifier: str,
) -> None:
    candidate = copy.deepcopy(valid_config)
    candidate["spatial"][field] = method_identifier
    validator = Draft202012Validator(case_schema, format_checker=FormatChecker())
    assert not list(validator.iter_errors(candidate))
