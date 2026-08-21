from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pandas as pd
import pytest
import yaml

from competition.intake import IntakeValidationError, validate_delivery


def _write_manifest(tmp_path: Path, datasets: list[dict], relations: list[dict] | None = None) -> Path:
    path = tmp_path / "intake.yaml"
    path.write_text(yaml.safe_dump({"manifest_version": "intake_manifest_v1", "source_root": ".", "datasets": datasets, "relations": relations or []}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def test_configured_null_and_small_negative_are_reported_without_source_change(tmp_path: Path) -> None:
    source = tmp_path / "loads.csv"
    pd.DataFrame({"hour": [0, 1, 2], "building_id": ["b1"] * 3, "heating_kW": [1.0, None, -0.0005]}).to_csv(source, index=False)
    before = sha256(source.read_bytes()).hexdigest()
    manifest = _write_manifest(tmp_path, [{"name": "loads", "path": "loads.csv", "format": "csv", "required_columns": ["hour", "building_id", "heating_kW"], "primary_key": ["hour", "building_id"], "null_policy": {"heating_kW": "fill_zero"}, "numeric": {"heating_kW": {"min": 0, "negative_tolerance": 0.001, "small_negative_policy": "clip_zero"}}, "time_index": {"column": "hour", "start": 0, "step": 1}}])
    report = validate_delivery(manifest)
    assert report.valid
    assert [item["action"] for item in report.normalizations] == ["fill_zero", "clip_zero"]
    assert sha256(source.read_bytes()).hexdigest() == before


def test_undeclared_null_and_material_negative_fail(tmp_path: Path) -> None:
    pd.DataFrame({"id": ["a", "b"], "value": [None, -2.0]}).to_csv(tmp_path / "data.csv", index=False)
    manifest = _write_manifest(tmp_path, [{"name": "data", "path": "data.csv", "required_columns": ["id", "value"], "primary_key": ["id"], "numeric": {"value": {"min": 0, "negative_tolerance": 0.001, "small_negative_policy": "clip_zero"}}}])
    report = validate_delivery(manifest)
    assert not report.valid
    assert {issue.code for issue in report.issues} >= {"NULL_VALUE", "VALUE_BELOW_MIN"}


def test_glob_validates_every_matching_file(tmp_path: Path) -> None:
    for index in range(2):
        pd.DataFrame({"hour": [0, 1], "value": [index, index + 1]}).to_csv(tmp_path / f"part{index}.csv", index=False)
    manifest = _write_manifest(tmp_path, [{"name": "parts", "glob": "part*.csv", "row_count": 2, "required_columns": ["hour", "value"], "primary_key": ["hour"], "time_index": {"column": "hour", "start": 0, "step": 1}}])
    report = validate_delivery(manifest)
    assert report.valid
    assert report.datasets["parts"]["file_count"] == 2


def test_cross_dataset_id_relation(tmp_path: Path) -> None:
    pd.DataFrame({"building_id": ["b1", "b2"]}).to_csv(tmp_path / "buildings.csv", index=False)
    pd.DataFrame({"building_id": ["b1", "b3"]}).to_csv(tmp_path / "loads.csv", index=False)
    manifest = _write_manifest(tmp_path, [{"name": "buildings", "path": "buildings.csv", "required_columns": ["building_id"]}, {"name": "loads", "path": "loads.csv", "required_columns": ["building_id"]}], [{"left": "loads", "right": "buildings", "column": "building_id", "mode": "equal"}])
    report = validate_delivery(manifest)
    assert not report.valid
    assert "ID_SET_MISMATCH" in {issue.code for issue in report.issues}


def test_relation_supports_different_column_names(tmp_path: Path) -> None:
    pd.DataFrame({"building_id": ["b1", "b2"]}).to_csv(tmp_path / "buildings.csv", index=False)
    pd.DataFrame({"from_id": ["b1"], "to_id": ["b2"]}).to_csv(tmp_path / "edges.csv", index=False)
    manifest = _write_manifest(tmp_path, [{"name": "buildings", "path": "buildings.csv", "required_columns": ["building_id"]}, {"name": "edges", "path": "edges.csv", "required_columns": ["from_id", "to_id"]}], [{"left": "edges", "left_column": "from_id", "right": "buildings", "right_column": "building_id", "mode": "subset"}, {"left": "edges", "left_column": "to_id", "right": "buildings", "right_column": "building_id", "mode": "subset"}])
    assert validate_delivery(manifest).valid


def test_unknown_manifest_key_fails_loudly(tmp_path: Path) -> None:
    manifest = tmp_path / "bad.yaml"
    manifest.write_text("manifest_version: intake_manifest_v1\ndatasets: []\ntypo: true\n", encoding="utf-8")
    with pytest.raises(IntakeValidationError, match="未知键"):
        validate_delivery(manifest)


def test_duplicate_manifest_key_and_path_escape_are_rejected(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text("manifest_version: intake_manifest_v1\nmanifest_version: intake_manifest_v1\ndatasets: []\n", encoding="utf-8")
    with pytest.raises(IntakeValidationError, match="重复"):
        validate_delivery(duplicate)
    outside = tmp_path.parent / "outside.csv"
    outside.write_text("id\na\n", encoding="utf-8")
    manifest = _write_manifest(tmp_path, [{"name": "outside", "path": "../outside.csv", "required_columns": ["id"]}])
    with pytest.raises(IntakeValidationError, match="越过"):
        validate_delivery(manifest)


def test_long_parameter_units_are_checked(tmp_path: Path) -> None:
    pd.DataFrame({"parameter": ["efficiency"], "unit": ["percent"]}).to_csv(tmp_path / "parameters.csv", index=False)
    manifest = _write_manifest(tmp_path, [{"name": "parameters", "path": "parameters.csv", "required_columns": ["parameter", "unit"], "units": {"parameter_column": "parameter", "unit_column": "unit", "expected": {"efficiency": "fraction"}, "allow_unlisted": False}}])
    report = validate_delivery(manifest)
    assert not report.valid
    assert "UNIT_MISMATCH" in {issue.code for issue in report.issues}

