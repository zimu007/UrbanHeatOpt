"""Deterministic v0.2 delivery to draft.2 canonical source adaptation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

import geopandas as gpd
import pandas as pd
import yaml

from competition.intake import WuhanV02Report, validate_wuhan_v02_delivery


@dataclass(frozen=True, slots=True)
class WuhanV02Adaptation:
    output_dir: Path
    buildings_path: Path
    archetype_map_path: Path
    loads_path: Path
    source_report_path: Path
    adaptation_report_path: Path
    field_mapping_path: Path
    data_version: str
    building_count: int
    hour_count: int


def _load_profile(profile_path: str | Path | None) -> dict[str, object]:
    path = (
        Path(profile_path)
        if profile_path is not None
        else Path(__file__).resolve().parents[1] / "configs" / "wuhan_v02.yaml"
    )
    profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(profile, dict) or profile.get("source_profile") != "wuhan_v02":
        raise ValueError("source profile 必须是 wuhan_v02")
    return profile


def _prepare_output(source: Path, output: Path) -> None:
    if output == source or output.is_relative_to(source):
        raise ValueError("适配输出目录不得位于源交付目录内部")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"适配输出目录必须为空或不存在: {output}")
    output.mkdir(parents=True, exist_ok=True)


def _normalize_archetype_map(source: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for row in source.itertuples(index=False):
        mixed = bool(row.is_mixed_use)
        records.append(
            {
                "building_id": str(row.building_id),
                "zone_id": str(row.zone_id) if mixed else f"{row.building_id}-01",
                "zone_use_type": str(row.zone_use_type) if mixed else str(row.use_type),
                "zone_area_m2": float(row.zone_area_m2) if mixed else float(row.target_conditioned_area_m2),
                "zone_archetype_id": str(row.zone_archetype_id) if mixed else str(row.archetype_id),
                "zone_scale_factor": float(row.zone_scale_factor) if mixed else float(row.scale_factor),
            }
        )
    result = pd.DataFrame.from_records(records).sort_values(
        ["building_id", "zone_id"], kind="stable"
    ).reset_index(drop=True)
    if result.duplicated(["building_id", "zone_id"]).any():
        raise ValueError("标准化原型映射 building_id+zone_id 不得重复")
    return result


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def adapt_wuhan_v02_sources(
    source_root: str | Path,
    output_dir: str | Path,
    *,
    profile_path: str | Path | None = None,
    full_audit: bool = True,
) -> WuhanV02Adaptation:
    """Create model-facing source tables without modifying the delivery."""

    source = Path(source_root).resolve()
    output = Path(output_dir).resolve()
    profile = _load_profile(profile_path)
    report: WuhanV02Report = validate_wuhan_v02_delivery(
        source,
        full_audit=full_audit,
        profile_path=profile_path,
    )
    if not report.valid:
        errors = [issue.message for issue in report.issues if issue.severity == "error"]
        raise ValueError("v0.2 源数据校验失败，禁止适配：" + "; ".join(errors))
    _prepare_output(source, output)

    data_version = str(profile["data_version"])
    source_files = profile["standard_files"]
    buildings_source = gpd.read_file(source / source_files["buildings"]["path"])
    buildings = buildings_source[
        ["building_id", "use_type", "conditioned_area_m2", "geometry"]
    ].rename(columns={"conditioned_area_m2": "heated_area_m2"})
    buildings = buildings.sort_values("building_id", kind="stable").reset_index(drop=True)
    buildings["terminal_type"] = "fan_coil"
    buildings["ventilation_system"] = "dedicated_fresh_air"
    buildings["fresh_air_load_included"] = True
    buildings["data_version"] = data_version

    source_mapping = pd.read_csv(
        source / source_files["building_archetype_map"]["path"],
        encoding="utf-8-sig",
    )
    mapping = _normalize_archetype_map(source_mapping)

    loads_source = pd.read_parquet(source / source_files["building_hourly_loads"]["path"])
    loads = loads_source[["timestamp", "building_id", "heating_kW"]].copy()
    loads["data_version"] = data_version
    loads = loads.sort_values(["timestamp", "building_id"], kind="stable").reset_index(drop=True)

    building_ids = set(buildings["building_id"])
    if set(mapping["building_id"]) != building_ids or set(loads["building_id"]) != building_ids:
        raise ValueError("适配后 buildings、archetype map、loads 建筑 ID 集合不一致")
    mapped_area = mapping.groupby("building_id")["zone_area_m2"].sum()
    heated_area = buildings.set_index("building_id")["heated_area_m2"]
    relative_area_error = ((mapped_area - heated_area).abs() / heated_area).max()
    if float(relative_area_error) > 0.001:
        raise ValueError("适配后原型分区面积与建筑 heated_area_m2 误差超过 0.1%")

    buildings_path = output / "buildings.geojson"
    mapping_path = output / "building_archetype_map.csv"
    loads_path = output / "building_hourly_loads.parquet"
    source_report_path = output / "source_validation_report.json"
    adaptation_report_path = output / "adaptation_report.json"
    field_mapping_path = output / "field_mapping.csv"
    buildings.to_file(buildings_path, driver="GeoJSON")
    mapping.to_csv(mapping_path, index=False, encoding="utf-8-sig")
    loads.to_parquet(loads_path, index=False)
    _write_json(source_report_path, report.to_dict())
    _write_json(
        adaptation_report_path,
        {
            "status": "adapted",
            "source_profile": report.source_profile,
            "profile_version": report.profile_version,
            "data_version": data_version,
            "building_count": len(buildings),
            "hour_count": int(loads["timestamp"].nunique()),
            "load_row_count": len(loads),
            "archetype_mapping_row_count": len(mapping),
            "mixed_use_building_count": int((source_mapping["is_mixed_use"] == True).groupby(source_mapping["building_id"]).any().sum()),
            "max_zone_area_relative_error": float(relative_area_error),
            "terminal_type": "fan_coil",
            "ventilation_system": "dedicated_fresh_air",
            "fresh_air_load_included": True,
            "fresh_air_load_added_by_adapter": False,
            "input_files_read_only": True,
            "execution_load_source": source_files["building_hourly_loads"]["path"],
            "raw_dest_role": "provenance_and_cross_check_only",
        },
    )
    pd.DataFrame(
        [
            {"source_file": "03_buildings.geojson", "source_field": "conditioned_area_m2", "canonical_file": "buildings.geojson", "canonical_field": "heated_area_m2", "rule": "rename_without_scaling"},
            {"source_file": "03_buildings.geojson", "source_field": "building_id", "canonical_file": "buildings.geojson", "canonical_field": "building_id", "rule": "identity"},
            {"source_file": "04_building_archetype_map.csv", "source_field": "zone_* or building archetype fields", "canonical_file": "building_archetype_map.csv", "canonical_field": "zone_*", "rule": "preserve_mixed_zones_and_expand_single_use"},
            {"source_file": "05_building_hourly_loads.parquet", "source_field": "heating_kW", "canonical_file": "building_hourly_loads.parquet", "canonical_field": "heating_kW", "rule": "identity_kW_th_no_fresh_air_addition"},
            {"source_file": "source_profile", "source_field": "data_version", "canonical_file": "all runtime tables", "canonical_field": "data_version", "rule": "stamp_profile_version"},
        ]
    ).to_csv(field_mapping_path, index=False, encoding="utf-8-sig")
    return WuhanV02Adaptation(
        output,
        buildings_path,
        mapping_path,
        loads_path,
        source_report_path,
        adaptation_report_path,
        field_mapping_path,
        data_version,
        len(buildings),
        int(loads["timestamp"].nunique()),
    )
