"""Regression tests for strict, read-only competition input validation."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import shutil

import pandas as pd
import pytest
import yaml

from urbanheatopt.data.validation.inputs import InputValidationError, validate_case_inputs


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINIMAL_CASE = PROJECT_ROOT / "tests" / "fixtures" / "minimal_case"


@pytest.fixture()
def case_copy(tmp_path: Path) -> Path:
    target = tmp_path / "minimal_case"
    shutil.copytree(MINIMAL_CASE, target)
    return target


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _codes(error: InputValidationError) -> set[str]:
    return {issue["code"] for issue in error.issues}


def test_valid_case_reports_hashes_and_does_not_modify_inputs(case_copy: Path) -> None:
    before = {
        path.relative_to(case_copy).as_posix(): _hash(path)
        for path in case_copy.rglob("*")
        if path.is_file()
    }

    inputs = validate_case_inputs(case_copy)

    after = {
        path.relative_to(case_copy).as_posix(): _hash(path)
        for path in case_copy.rglob("*")
        if path.is_file()
    }
    assert before == after
    assert set(inputs.file_sha256) == {
        "building_hourly_loads.parquet",
        "buildings.geojson",
        "case_config.yaml",
        "external_timeseries.parquet",
        "roads_or_feasible_space.geojson",
        "technologies.csv",
    }
    assert inputs.file_sha256 == {
        name: before[name] for name in inputs.file_sha256
    }


def test_duplicate_yaml_key_has_stable_error_code(case_copy: Path) -> None:
    config_path = case_copy / "case_config.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\ncase_id: duplicate\n",
        encoding="utf-8",
    )

    with pytest.raises(InputValidationError) as captured:
        validate_case_inputs(case_copy)

    assert "CONFIG_DUPLICATE_KEY" in _codes(captured.value)


def test_id_whitespace_is_rejected_without_auto_trim(case_copy: Path) -> None:
    path = case_copy / "building_hourly_loads.parquet"
    loads = pd.read_parquet(path)
    loads.loc[loads["building_id"] == loads["building_id"].iloc[0], "building_id"] = " bad_id"
    loads.to_parquet(path, index=False)
    before = _hash(path)

    with pytest.raises(InputValidationError) as captured:
        validate_case_inputs(case_copy)

    assert "ID_INVALID" in _codes(captured.value)
    assert _hash(path) == before


def test_wrong_unit_is_rejected_without_guessing_conversion(case_copy: Path) -> None:
    path = case_copy / "case_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["units"]["heating_power"] = "W"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    before = _hash(path)

    with pytest.raises(InputValidationError) as captured:
        validate_case_inputs(case_copy)

    assert "CONFIG_SCHEMA_ERROR" in _codes(captured.value)
    assert _hash(path) == before


def test_misspelled_field_is_not_auto_renamed(case_copy: Path) -> None:
    path = case_copy / "technologies.csv"
    technologies = pd.read_csv(path).rename(
        columns={"technology_id": "technologyid"}
    )
    technologies.to_csv(path, index=False)
    before = _hash(path)

    with pytest.raises(InputValidationError) as captured:
        validate_case_inputs(case_copy)

    assert "FILE_OR_FIELD_MISSING" in _codes(captured.value)
    assert _hash(path) == before


def test_unsupported_feature_has_explicit_error_code(case_copy: Path) -> None:
    path = case_copy / "technologies.csv"
    technologies = pd.read_csv(path)
    technologies.loc[0, "technology_type"] = "air_source_heat_pump"
    technologies.loc[0, "energy_carrier"] = "electricity"
    technologies.loc[0, "cop"] = 3.0
    technologies.to_csv(path, index=False)

    with pytest.raises(InputValidationError) as captured:
        validate_case_inputs(case_copy)

    assert "FEATURE_NOT_IMPLEMENTED" in _codes(captured.value)
