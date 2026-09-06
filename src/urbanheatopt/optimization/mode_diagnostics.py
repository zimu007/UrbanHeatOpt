"""Read-only B3 diagnostics for requested versus physically realized modes."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from math import isfinite
from pathlib import Path
from typing import Mapping

import pandas as pd


REAL_COST_COMPONENTS = (
    'heat_source_capex', 'hp_boiler_fom', 'electricity_energy_cost', 'gas_cost',
    'monthly_demand_charge', 'connection_annualized_cost', 'pipe_annualized_cost',
    'pumping_electricity_cost', 'tes_related_cost',
)


def classify_realized_mode(connected: Mapping[str, float], *, tolerance: float = 1e-6) -> str:
    if not connected:
        raise ValueError('connected decisions are missing')
    if tolerance < 0 or not isfinite(tolerance):
        raise ValueError('tolerance must be finite and nonnegative')
    values = tuple(float(value) for value in connected.values())
    if any(not isfinite(value) or min(abs(value), abs(value - 1)) > tolerance for value in values):
        raise ValueError('connected decisions must be binary within tolerance')
    count = sum(value > .5 for value in values)
    if count == len(values):
        return 'PURE_CENTRAL'
    if count == 0:
        return 'PURE_DISTRIBUTED'
    return 'TRUE_HYBRID'


@dataclass(frozen=True, slots=True)
class ModeDiagnostic:
    requested_mode: str
    realized_mode: str
    connected_building_count: int
    local_building_count: int
    connected: Mapping[str, float]
    selected_site: str | None
    tree_summary: Mapping
    selected_pipe_summary: Mapping
    annual_cost_breakdown: Mapping[str, float]
    annual_real_cost_CNY_per_year: float
    annual_carbon_breakdown: Mapping[str, float]
    annual_physical_carbon_kgCO2e_per_year: float
    monthly_demand_charge_CNY_per_year: float | None
    solver_status: str
    termination_condition: str
    solver_gap: float | None
    case_bundle_id: str | None
    case_sha256: str | None
    parameter_version: str | None
    snapshot_id: str | None
    network_hash: str | None
    tree_hash: str | None
    objective: str | None
    carbon_cap_kgCO2e_per_year: float | None


def make_mode_diagnostic(*, requested_mode: str, connected: Mapping[str, float],
                         annual_cost_breakdown: Mapping[str, float],
                         annual_real_cost_CNY_per_year: float,
                         annual_carbon_breakdown: Mapping[str, float],
                         annual_physical_carbon_kgCO2e_per_year: float, **metadata) -> ModeDiagnostic:
    realized = classify_realized_mode(connected)
    connected_count = sum(float(value) > .5 for value in connected.values())
    return ModeDiagnostic(
        requested_mode=requested_mode, realized_mode=realized,
        connected_building_count=connected_count,
        local_building_count=len(connected) - connected_count,
        connected=dict(connected), annual_cost_breakdown=dict(annual_cost_breakdown),
        annual_real_cost_CNY_per_year=float(annual_real_cost_CNY_per_year),
        annual_carbon_breakdown=dict(annual_carbon_breakdown),
        annual_physical_carbon_kgCO2e_per_year=float(annual_physical_carbon_kgCO2e_per_year),
        selected_site=metadata.get('selected_site'), tree_summary=dict(metadata.get('tree_summary', {})),
        selected_pipe_summary=dict(metadata.get('selected_pipe_summary', {})),
        monthly_demand_charge_CNY_per_year=metadata.get('monthly_demand_charge_CNY_per_year'),
        solver_status=metadata.get('solver_status', 'unknown'),
        termination_condition=metadata.get('termination_condition', 'unknown'),
        solver_gap=metadata.get('solver_gap'), case_bundle_id=metadata.get('case_bundle_id'),
        case_sha256=metadata.get('case_sha256'), parameter_version=metadata.get('parameter_version'),
        snapshot_id=metadata.get('snapshot_id'), network_hash=metadata.get('network_hash'),
        tree_hash=metadata.get('tree_hash'), objective=metadata.get('objective'),
        carbon_cap_kgCO2e_per_year=metadata.get('carbon_cap_kgCO2e_per_year'))


def compare_mode_diagnostics(connected_baseline: ModeDiagnostic,
                             local_alternative: ModeDiagnostic,
                             *, tolerance_CNY: float = 1e-5) -> dict:
    checks = {
        'case_bundle': (connected_baseline.case_bundle_id or connected_baseline.case_sha256)
                       == (local_alternative.case_bundle_id or local_alternative.case_sha256),
        'parameter_snapshot': (connected_baseline.parameter_version, connected_baseline.snapshot_id)
                              == (local_alternative.parameter_version, local_alternative.snapshot_id),
        'site': connected_baseline.selected_site == local_alternative.selected_site,
        'network': connected_baseline.network_hash == local_alternative.network_hash,
        'tree': connected_baseline.tree_hash == local_alternative.tree_hash,
        'objective': connected_baseline.objective == local_alternative.objective,
        'carbon_cap': connected_baseline.carbon_cap_kgCO2e_per_year
                      == local_alternative.carbon_cap_kgCO2e_per_year,
    }
    matched = all(checks.values())
    shared = set(connected_baseline.annual_cost_breakdown) & set(local_alternative.annual_cost_breakdown)
    cost_delta = {key: float(local_alternative.annual_cost_breakdown[key])
                  - float(connected_baseline.annual_cost_breakdown[key]) for key in sorted(shared)}
    total_delta = (local_alternative.annual_real_cost_CNY_per_year
                   - connected_baseline.annual_real_cost_CNY_per_year)
    complete = set(REAL_COST_COMPONENTS) <= shared
    if complete and abs(sum(cost_delta[key] for key in REAL_COST_COMPONENTS) - total_delta) > tolerance_CNY:
        raise ValueError('cost component deltas do not reconcile to total delta')
    carbon_shared = set(connected_baseline.annual_carbon_breakdown) & set(local_alternative.annual_carbon_breakdown)
    return {
        'matched_comparison': matched, 'match_checks': checks,
        'different_fields': tuple(key for key, equal in checks.items() if not equal),
        'changed_buildings': tuple(sorted(
            building for building in set(connected_baseline.connected) | set(local_alternative.connected)
            if connected_baseline.connected.get(building) != local_alternative.connected.get(building))),
        'cost_component_delta_CNY_per_year': cost_delta,
        'cost_decomposition_complete': complete,
        'delta_cost_CNY_per_year': total_delta,
        'monthly_demand_charge_delta_CNY_per_year': (
            None if connected_baseline.monthly_demand_charge_CNY_per_year is None
            or local_alternative.monthly_demand_charge_CNY_per_year is None else
            local_alternative.monthly_demand_charge_CNY_per_year
            - connected_baseline.monthly_demand_charge_CNY_per_year),
        'carbon_component_delta_kgCO2e_per_year': {
            key: float(local_alternative.annual_carbon_breakdown[key])
            - float(connected_baseline.annual_carbon_breakdown[key]) for key in sorted(carbon_shared)},
        'delta_carbon_kgCO2e_per_year': (
            local_alternative.annual_physical_carbon_kgCO2e_per_year
            - connected_baseline.annual_physical_carbon_kgCO2e_per_year),
    }


def load_compact_task_diagnostic(task_root: str | Path, *, run_root: str | Path) -> ModeDiagnostic:
    """Read an existing compact task; never builds or solves a model."""
    task_root, run_root = Path(task_root), Path(run_root)
    success = json.loads((task_root / 'success.json').read_text(encoding='utf-8'))
    checkpoint = json.loads((run_root / success['checkpoint_file']).read_text(encoding='utf-8'))
    point, meta = success['point'], success['model_metadata']
    case_payload = json.loads((run_root / 'case.json').read_text(encoding='utf-8'))
    return make_mode_diagnostic(
        requested_mode=success['mode'], connected=checkpoint['connected'],
        annual_cost_breakdown={}, annual_real_cost_CNY_per_year=point['annual_real_cost_CNY_per_year'],
        annual_carbon_breakdown={},
        annual_physical_carbon_kgCO2e_per_year=point['annual_operating_carbon_kgCO2e_per_year'],
        selected_site=success.get('site_id'), tree_summary=success.get('tree_design_summary', {}),
        selected_pipe_summary=checkpoint.get('variable_grade_selected', {}),
        monthly_demand_charge_CNY_per_year=None,
        solver_status=point.get('solver_status', 'unknown'),
        termination_condition=point.get('termination_condition', 'unknown'),
        solver_gap=point.get('reported_mip_gap'), case_sha256=success.get('case_sha256'),
        parameter_version=case_payload.get('parameter_version'),
        snapshot_id=case_payload.get('parameter_version'),
        network_hash=hashlib.sha256(json.dumps(
            case_payload.get('network'), sort_keys=True, separators=(',', ':'),
            ensure_ascii=False).encode('utf-8')).hexdigest(),
        tree_hash=success.get('tree_design_summary', {}).get('design_certificate_sha256'),
        objective=meta.get('objective'), carbon_cap_kgCO2e_per_year=point.get('epsilon_kgCO2e_per_year'))
