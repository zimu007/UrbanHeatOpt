"""B-owned B2 engineering-boundary projection.

This module consumes already-standardized records.  It never reads delivery
files and never derives capacity from equipment ratings, DN, demand, or an
older run.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Mapping

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
