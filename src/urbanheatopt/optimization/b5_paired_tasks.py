"""Fail-closed task contracts for full-season matched TES comparisons.

This module prepares metadata only.  It neither builds a mathematical model nor
invokes a solver.  The existing compact full-season scan is intentionally not
accepted as a B5 comparison because its TES branches may choose different sites,
connections, and networks.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping


B5_SCHEMA = "urbanheatopt_b5_tes_paired_tasks_1"
REVISED_PARAMETER_VERSION = "revised_20260831"
POINT_ROLES = ("economic", "knee", "low_carbon")
PREPARED_NOT_RUN = "PREPARED_NOT_RUN"
NOT_READY = "NOT_READY"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

FIXED_CONDITIONS = (
    "case_bundle_id", "parameter_version", "snapshot_id", "site_id",
    "connection_vector", "connection_hash", "network_hash", "tree_hash",
    "demand_hash", "objective", "carbon_cap_kgCO2e_per_year",
    "b2_boundary_hash", "solver_settings", "solver_settings_hash",
)
TES_OFF_REOPTIMIZATION = (
    "central_hp_capacity", "boiler_capacity", "hourly_dispatch",
)
TES_ON_REOPTIMIZATION = TES_OFF_REOPTIMIZATION + (
    "tes_energy_capacity", "tes_charge_capacity", "tes_discharge_capacity",
    "tes_hourly_charge", "tes_hourly_discharge", "tes_hourly_soc",
)


class B5ContractError(ValueError):
    """A paired-comparison invariant or formal readiness gate failed."""


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise B5ContractError(f"value is not canonical JSON: {exc}") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise B5ContractError(f"{name} must be a lowercase SHA-256")


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise B5ContractError(f"{name} must be non-empty")


@dataclass(frozen=True, slots=True)
class B5ReadinessEvidence:
    """Evidence required before a formal 2160-hour B5 pair may run."""

    hour_count: int
    parameter_version: str
    b1_real_bundle_smoke_pass: bool
    b2_capacity_boundary_complete: bool
    pipe_thermal_capacity_complete: bool
    electricity_scope_approved: bool
    tes_engineering_maxima_approved: bool
    unresolved_required_fields: tuple[str, ...]
    model_ready: bool
    result_class: str = "publication"
    tes_boundary_status: str = "verified"

    def blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if self.result_class not in ("research", "publication"):
            blockers.append("invalid_result_class")
        if self.tes_boundary_status not in ("research_assumption", "verified"):
            blockers.append("invalid_tes_boundary_status")
        if self.hour_count != 2160:
            blockers.append("full_2160h_coverage")
        if self.parameter_version != REVISED_PARAMETER_VERSION:
            blockers.append("revised_20260831_snapshot")
        for ready, name in (
            (self.b1_real_bundle_smoke_pass, "b1_real_bundle_smoke_test"),
            (self.b2_capacity_boundary_complete, "b2_capacity_boundary"),
            (self.pipe_thermal_capacity_complete, "pipe_thermal_capacity"),
            (self.electricity_scope_approved, "approved_electricity_scope"),
            (self.model_ready, "model_ready"),
        ):
            if not ready:
                blockers.append(name)
        tes_ready = self.tes_engineering_maxima_approved or (
            self.result_class == "research"
            and self.tes_boundary_status == "research_assumption"
        )
        if not tes_ready:
            blockers.append("approved_tes_engineering_maxima")
        if self.result_class == "publication" and self.tes_boundary_status != "verified":
            blockers.append("publication_requires_verified_tes_boundary")
        if self.unresolved_required_fields:
            blockers.append("unresolved_required_fields:" + ",".join(
                sorted(self.unresolved_required_fields)))
        return tuple(blockers)

    @property
    def ready(self) -> bool:
        return not self.blockers()


@dataclass(frozen=True, slots=True)
class B5FixedBoundary:
    """Fields that must be byte-for-byte equivalent across TES OFF and ON."""

    point_role: str
    source_point_id: str
    case_bundle_id: str
    parameter_version: str
    snapshot_id: str
    site_id: str
    connection_vector: Mapping[str, int]
    connection_hash: str
    network_hash: str
    tree_hash: str
    demand_hash: str
    objective: str
    carbon_cap_kgCO2e_per_year: float | None
    b2_boundary_hash: str
    solver_settings: Mapping[str, Any]
    solver_settings_hash: str

    def __post_init__(self) -> None:
        if self.point_role not in POINT_ROLES:
            raise B5ContractError(f"unsupported point_role: {self.point_role}")
        for name in ("source_point_id", "parameter_version", "site_id", "objective"):
            _require_text(getattr(self, name), name)
        for name in ("case_bundle_id", "snapshot_id", "connection_hash",
                     "network_hash", "tree_hash", "demand_hash",
                     "b2_boundary_hash", "solver_settings_hash"):
            _require_hash(getattr(self, name), name)
        connection = dict(self.connection_vector)
        if not connection or any(value not in (0, 1) for value in connection.values()):
            raise B5ContractError("connection_vector must be a non-empty binary mapping")
        if canonical_sha256(connection) != self.connection_hash:
            raise B5ContractError("connection_hash does not match connection_vector")
        if not isinstance(self.solver_settings, Mapping) or not self.solver_settings:
            raise B5ContractError("solver_settings must be a non-empty mapping")
        if canonical_sha256(dict(self.solver_settings)) != self.solver_settings_hash:
            raise B5ContractError("solver_settings_hash does not match solver_settings")
        cap = self.carbon_cap_kgCO2e_per_year
        if cap is not None and (isinstance(cap, bool) or not isinstance(cap, (int, float))
                                or not math.isfinite(float(cap)) or cap < 0):
            raise B5ContractError("carbon cap must be null or finite and nonnegative")


@dataclass(frozen=True, slots=True)
class B5TaskTemplate:
    task_id: str
    group_id: str
    fixed: B5FixedBoundary
    tes_enabled: bool
    execution_status: str
    fixed_conditions: tuple[str, ...]
    allowed_reoptimization: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class B5ComparisonGroup:
    schema: str
    group_id: str
    point_role: str
    source_point_id: str
    readiness: B5ReadinessEvidence
    readiness_status: str
    readiness_blockers: tuple[str, ...]
    tes_off: B5TaskTemplate
    tes_on: B5TaskTemplate


def make_b5_comparison_group(*, fixed: B5FixedBoundary,
                             readiness: B5ReadinessEvidence) -> B5ComparisonGroup:
    """Create a non-running pair template and retain all readiness blockers."""

    if readiness.parameter_version != fixed.parameter_version:
        raise B5ContractError("readiness/fixed parameter_version mismatch")
    group_id = "b5-" + canonical_sha256({
        "schema": B5_SCHEMA, "fixed": asdict(fixed),
        "readiness": asdict(readiness),
    })[:24]
    status = PREPARED_NOT_RUN if readiness.ready else NOT_READY
    off = B5TaskTemplate(
        task_id=f"{group_id}-tes-off", group_id=group_id, fixed=fixed,
        tes_enabled=False, execution_status=status,
        fixed_conditions=FIXED_CONDITIONS,
        allowed_reoptimization=TES_OFF_REOPTIMIZATION,
    )
    on = B5TaskTemplate(
        task_id=f"{group_id}-tes-on", group_id=group_id, fixed=fixed,
        tes_enabled=True, execution_status=status,
        fixed_conditions=FIXED_CONDITIONS,
        allowed_reoptimization=TES_ON_REOPTIMIZATION,
    )
    group = B5ComparisonGroup(
        schema=B5_SCHEMA, group_id=group_id, point_role=fixed.point_role,
        source_point_id=fixed.source_point_id, readiness=readiness,
        readiness_status=status, readiness_blockers=readiness.blockers(),
        tes_off=off, tes_on=on,
    )
    validate_b5_pair(off, on)
    return group


def validate_b5_pair(off: B5TaskTemplate, on: B5TaskTemplate) -> None:
    """Reject any OFF/ON comparison whose difference exceeds TES enablement."""

    if off.group_id != on.group_id:
        raise B5ContractError("group_id mismatch")
    if off.task_id == on.task_id:
        raise B5ContractError("OFF and ON task IDs must differ")
    if off.tes_enabled is not False or on.tes_enabled is not True:
        raise B5ContractError("pair must contain TES OFF then TES ON")
    mismatches = [name for name in FIXED_CONDITIONS
                  if getattr(off.fixed, name) != getattr(on.fixed, name)]
    if off.fixed.point_role != on.fixed.point_role:
        mismatches.append("point_role")
    if off.fixed.source_point_id != on.fixed.source_point_id:
        mismatches.append("source_point_id")
    if mismatches:
        raise B5ContractError("non-matched TES pair: " + ", ".join(mismatches))


def assert_b5_runnable(group: B5ComparisonGroup) -> None:
    """Explicit execution gate; this function performs no execution."""

    validate_b5_pair(group.tes_off, group.tes_on)
    if not group.readiness.ready:
        raise B5ContractError("B5 is NOT_READY: " + ", ".join(group.readiness.blockers()))
    if group.readiness_status != PREPARED_NOT_RUN:
        raise B5ContractError("B5 group is not in PREPARED_NOT_RUN state")


@dataclass(frozen=True, slots=True)
class B5TaskOutcome:
    task_id: str
    annual_real_cost_CNY_per_year: float
    physical_carbon_kgCO2e_per_year: float
    electricity_energy_cost_CNY_per_year: float
    gas_cost_CNY_per_year: float
    monthly_demand_charge_CNY_per_year: float
    hp_capex_CNY_per_year: float
    boiler_capex_CNY_per_year: float
    pipe_connection_cost_CNY_per_year: float
    tes_capex_CNY_per_year: float
    heat_source_capacities_kW: Mapping[str, float]
    tes_capacities: Mapping[str, float]
    peak_purchased_electricity_kW: float
    unmet_heat_kWh: float
    solver_status: str
    solver_gap: float
    hourly_coverage: int


def compare_b5_outcomes(group: B5ComparisonGroup, off: B5TaskOutcome,
                        on: B5TaskOutcome) -> dict[str, float]:
    """Compare future qualified results; never reads a run or calls a solver."""

    validate_b5_pair(group.tes_off, group.tes_on)
    if off.task_id != group.tes_off.task_id or on.task_id != group.tes_on.task_id:
        raise B5ContractError("outcome task IDs do not identify this pair")
    if off.hourly_coverage != 2160 or on.hourly_coverage != 2160:
        raise B5ContractError("B5 outcomes require full 2160-hour coverage")
    if not math.isclose(off.pipe_connection_cost_CNY_per_year,
                        on.pipe_connection_cost_CNY_per_year,
                        rel_tol=1e-12, abs_tol=1e-6):
        raise B5ContractError(
            "fixed pipe/connection annualized cost differs across the TES pair")
    return {
        "delta_cost_CNY_per_year": (
            on.annual_real_cost_CNY_per_year - off.annual_real_cost_CNY_per_year),
        "delta_carbon_kgCO2e_per_year": (
            on.physical_carbon_kgCO2e_per_year - off.physical_carbon_kgCO2e_per_year),
        "delta_demand_charge_CNY_per_year": (
            on.monthly_demand_charge_CNY_per_year
            - off.monthly_demand_charge_CNY_per_year),
    }


__all__ = [
    "B5_SCHEMA", "B5ContractError", "B5ReadinessEvidence", "B5FixedBoundary",
    "B5TaskTemplate", "B5ComparisonGroup", "B5TaskOutcome",
    "FIXED_CONDITIONS", "TES_OFF_REOPTIMIZATION", "TES_ON_REOPTIMIZATION",
    "canonical_sha256", "make_b5_comparison_group", "validate_b5_pair",
    "assert_b5_runnable", "compare_b5_outcomes",
]
