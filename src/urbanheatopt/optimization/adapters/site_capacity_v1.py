"""B-owned B2 engineering-boundary projection.

This module consumes already-standardized records.  It never reads delivery
files and never derives capacity from equipment ratings, DN, demand, or an
older run.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Mapping

from urbanheatopt.data.capacity_boundaries import CAPACITY_BOUNDARY_VERSION

from urbanheatopt.model.road_core import (
    B2CapacityInput, BoundaryEvidence, PipeCapacityBoundary, RoadCase,
    SiteCapacityBoundary, validate_case,
)


def build_b2_capacity_input(
    *,
    site_records: Iterable[Mapping],
    pipe_records: Iterable[Mapping],
    local_hp_capacity_max_kW_th_by_building: Mapping[str, float] | None = None,
    local_hp_evidence: Mapping | None = None,
) -> B2CapacityInput:
    """Create the typed projection without weakening any missing boundary."""
    def evidence(row: Mapping) -> BoundaryEvidence:
        return BoundaryEvidence(str(row.get('source', '')), str(row.get('status', '')),
                                str(row.get('evidence_id', '')))

    sites = tuple(SiteCapacityBoundary(
        site_id=str(row['site_id']),
        allowed_technology_ids=frozenset(row['allowed_technology_ids']),
        total_heat_capacity_max_kW_th=row.get('total_heat_capacity_max_kW_th'),
        technology_capacity_max_kW_th=dict(row['technology_capacity_max_kW_th']),
        electricity_connection_max_kW_e=row.get('electricity_connection_max_kW_e'),
        electricity_connection_scope=row.get('electricity_connection_scope'),
        gas_connection_max_kW_LHV=row.get('gas_connection_max_kW_LHV'),
        evidence=evidence(row),
    ) for row in site_records)
    pipes = tuple(PipeCapacityBoundary(
        pipe_type_id=str(row['pipe_type_id']), capacity_kW_th=row.get('capacity_kW_th'),
        evidence=evidence(row),
    ) for row in pipe_records)
    return B2CapacityInput(
        sites=sites, pipes=pipes,
        local_hp_capacity_max_kW_th_by_building=local_hp_capacity_max_kW_th_by_building,
        local_hp_evidence=(evidence(local_hp_evidence) if local_hp_evidence else None),
    )


def consume_capacity_boundary_snapshot(payload: Mapping[str, Any]) -> B2CapacityInput:
    """Validate A's machine-readable snapshot and project it to B2 types.

    DN reference capacities deliberately remain outside this projection.  Only
    the planning ``capacity_kW_th`` field is executable in the research model.
    """
    if payload.get("schema_version") != CAPACITY_BOUNDARY_VERSION:
        raise ValueError(f"容量边界版本必须为{CAPACITY_BOUNDARY_VERSION}")
    if payload.get("building_count") != 62 or payload.get("hour_count") != 2160:
        raise ValueError("B2研究边界必须对应62栋×2160小时")
    sites = payload.get("sites")
    pipes = payload.get("pipes")
    local = payload.get("local_hp")
    tes = payload.get("tes")
    if not isinstance(sites, list) or len(sites) != 5:
        raise ValueError("B2容量快照必须包含5个候选站")
    if not isinstance(pipes, list) or len(pipes) != 3:
        raise ValueError("B2容量快照必须包含3个规划管型")
    if not isinstance(local, Mapping) or not isinstance(
        local.get("capacity_max_kW_th_by_building"), Mapping
    ) or len(local["capacity_max_kW_th_by_building"]) != 62:
        raise ValueError("B2容量快照必须完整覆盖62栋分布式热泵")
    if not isinstance(tes, Mapping) or any(
        not isinstance(tes.get(name), (int, float)) or tes[name] <= 0
        for name in (
            "energy_capacity_max_kWh_th",
            "charge_capacity_max_kW_th",
            "discharge_capacity_max_kW_th",
        )
    ):
        raise ValueError("B2容量快照缺少TES能量或充放功率上限")
    for row in pipes:
        if row.get("reference_capacity_consumed") is not False:
            raise ValueError("DN物理参考容量不得进入B2执行边界")
        if row.get("pipe_design_status") != "planning_capacity_tier_not_hydraulic_dn":
            raise ValueError("执行管型必须明确标记为规划容量代理档")
    return build_b2_capacity_input(
        site_records=sites,
        pipe_records=pipes,
        local_hp_capacity_max_kW_th_by_building={
            str(key): float(value)
            for key, value in local["capacity_max_kW_th_by_building"].items()
        },
        local_hp_evidence=local,
    )


def apply_b2_capacity_input(case: RoadCase, boundary: B2CapacityInput) -> RoadCase:
    """Apply approved pipe capacities and attach B2 limits; validation is fail-closed."""
    pipe_by_id = {row.pipe_type_id: row for row in boundary.pipes}
    if set(pipe_by_id) != {row.pipe_type_id for row in case.pipe_designs}:
        raise ValueError('B2 pipe IDs do not match RoadCase pipe designs')
    if any(row.capacity_kW_th is None for row in pipe_by_id.values()):
        raise ValueError('approved pipe thermal capacity is missing')
    projected = replace(
        case,
        pipe_designs=tuple(replace(row, capacity_kW_th=pipe_by_id[row.pipe_type_id].capacity_kW_th)
                           for row in case.pipe_designs),
        b2_capacity=boundary,
    )
    validate_case(projected)
    return projected
