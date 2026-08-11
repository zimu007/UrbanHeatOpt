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
SCHEMA_DIRECTORY = REPOSITORY_ROOT / "competition" / "schemas"


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


def test_machine_contract_is_parseable_and_has_no_duplicate_keys() -> None:
    contract_path = SCHEMA_DIRECTORY / "input_contract.yaml"
    raw = contract_path.read_text(encoding="utf-8")
    syntax_tree = yaml.compose(raw)
    assert syntax_tree is not None
    _assert_unique_yaml_keys(syntax_tree)
    contract = yaml.safe_load(raw)

    assert contract["contract_version"] == "competition_input_v1"
    assert contract["canonical_units"]["heating_power"] == "kW"
    external_columns = contract["files"]["external_timeseries"]["required_columns"]
    assert "timestamp" in external_columns
    assert "data_version" in external_columns
    assert contract["legacy_adapter_contract"]["unit_conversion_count"] == 0
    assert contract["technology_support_status"]["contract_executable_types"] == [
        "fixed_heat_source"
    ]
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
