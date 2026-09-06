"""Synthetic tests for the 0906 supplement; no real delivery data is committed."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from urbanheatopt.parameters.capacity_supplement_0906 import (
    PIPE_IDS,
    SUPPLEMENT_FILES,
    read_capacity_supplement_0906,
)
from urbanheatopt.parameters.legacy_economics import PackageError


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def supplement(tmp_path: Path) -> tuple[Path, Path]:
    base = tmp_path / "pipe_types.csv"
    base.write_text("synthetic authoritative pipe table\n", encoding="utf-8")
    root = tmp_path / "0906回复缺失清单"
    root.mkdir()
    (root / "pipe_types.csv").write_bytes(base.read_bytes())
    (root / "UrbanHeatOpt_V2缺失参数处理与冻结说明_20260906.md").write_text(
        "synthetic decision document", encoding="utf-8"
    )
    capacities = [10.0, 20.0, 30.0]
    dns = [100.0, 200.0, 300.0]
    capacity_rows = []
    reviewed_rows = []
    for pipe_id, dn, capacity in zip(PIPE_IDS, dns, capacities):
        capacity_rows.append({
            "pipe_type_id": pipe_id, "dn_mm": dn, "working_pipe_od_mm": dn + 10,
            "wall_thickness_mm": 5, "inner_diameter_mm": dn,
            "design_deltaT_K": 5, "capacity_kW_th": capacity,
            "capacity_upper_100Pa_m_kW_th": capacity * 1.2,
            "parameter_status": "research_reference", "source_id": "SYN",
            "geometry_source_url": "https://example.invalid/geometry",
            "hydraulic_standard_url": "https://example.invalid/hydraulic",
        })
        reviewed_rows.append({
            "pipe_type_id": pipe_id, "dn_mm": dn,
            "working_pipe_od_mm_recommended": dn + 10,
            "wall_thickness_mm_recommended": 5,
            "inner_diameter_mm_recommended": dn, "design_deltaT_K": 5,
            "capacity_baseline_kW_th": capacity,
            "capacity_upper_kW_th": capacity * 1.2,
            "geometry_source_url": "https://example.invalid/geometry",
            "hydraulic_standard_url": "https://example.invalid/hydraulic",
            "review_status": "recommended_planning_reference",
        })
    _write(root / "pipe_capacity_limits.csv", capacity_rows)
    _write(root / "pipe_types_reviewed_v2.csv", reviewed_rows)
    assert {path.name for path in root.iterdir()} == set(SUPPLEMENT_FILES)
    return root, base


def test_reference_capacity_is_never_execution_capacity(supplement):
    root, base = supplement
    result = read_capacity_supplement_0906(root, authoritative_pipe_types=base)
    assert result["supplement_version"] == "capacity_supplement_20260906"
    assert [row["reference_capacity_kW_th"] for row in result["reference_pipe_capacities"]] == [10, 20, 30]
    assert all(not row["execution_use_allowed"] for row in result["reference_pipe_capacities"])
    policy = result["execution_capacity_policy"]
    assert policy["reference_capacity_consumed"] is False
    assert list(policy["factors_of_margin_peak"].values()) == [0.6, 1.0, 1.5]
    assert policy["pipe_design_status"] == "planning_capacity_tier_not_hydraulic_dn"


def test_rejects_unclassified_file(supplement):
    root, base = supplement
    (root / "unexpected.csv").write_text("x\n", encoding="utf-8")
    with pytest.raises(PackageError, match="未分类"):
        read_capacity_supplement_0906(root, authoritative_pipe_types=base)


def test_rejects_authoritative_pipe_copy_mismatch(supplement):
    root, base = supplement
    (root / "pipe_types.csv").write_text("changed\n", encoding="utf-8")
    with pytest.raises(PackageError, match="SHA-256"):
        read_capacity_supplement_0906(root, authoritative_pipe_types=base)


def test_rejects_reviewed_capacity_mismatch(supplement):
    root, base = supplement
    rows = list(csv.DictReader((root / "pipe_types_reviewed_v2.csv").open(encoding="utf-8-sig")))
    rows[0]["capacity_baseline_kW_th"] = "999"
    _write(root / "pipe_types_reviewed_v2.csv", rows)
    with pytest.raises(PackageError, match="capacity_baseline"):
        read_capacity_supplement_0906(root, authoritative_pipe_types=base)
