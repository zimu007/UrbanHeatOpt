"""Numerically stable result semantics for thermal-energy storage exports.

The optimization model intentionally permits a zero-sized TES.  When TES has
no fixed installation cost, its binary installation variable may therefore be
one even though every capacity and every hourly flow is zero.  That is a valid
degenerate solver solution, but it must not be reported as a real installation.

This module only classifies and cleans exported values.  It does not alter the
mathematical model, its feasible region, or its objective function.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


TES_RESULT_TOLERANCE = 1e-6


def clean_near_zero(value: float, *, tolerance: float = TES_RESULT_TOLERANCE) -> float:
    """Return an ordinary zero for finite values within the result tolerance."""

    number = float(value)
    if not isfinite(number):
        raise ValueError("TES result value must be finite")
    if not isfinite(tolerance) or tolerance < 0:
        raise ValueError("TES result tolerance must be finite and nonnegative")
    return 0.0 if abs(number) <= tolerance else number


def activity_flag(value: float, *, tolerance: float = TES_RESULT_TOLERANCE) -> bool:
    """Classify a positive hourly TES flow after numerical-noise cleanup."""

    return clean_near_zero(value, tolerance=tolerance) > 0.0


@dataclass(frozen=True, slots=True)
class TESSemantics:
    """Separation of a raw solver binary from physical installation and use."""

    solver_built_binary: float
    tes_installed: bool
    tes_used: bool
    semantic_built: int


def classify_tes_semantics(
    *,
    solver_built_binary: float,
    energy_capacity_kWh_th: float,
    charge_capacity_kW_th: float,
    discharge_capacity_kW_th: float,
    power_cost_capacity_kW_th: float,
    actual_peak_charge_kW_th: float,
    actual_peak_discharge_kW_th: float,
    tolerance: float = TES_RESULT_TOLERANCE,
) -> TESSemantics:
    """Classify a TES without treating a degenerate binary as construction.

    ``tes_installed`` is driven by physical design capacity (or actual use as a
    defensive fallback). ``tes_used`` is driven by hourly charge/discharge.
    ``semantic_built`` preserves the historical ``built`` output column while
    giving it the physically meaningful interpretation expected by consumers.
    The untouched raw solver value is retained in ``solver_built_binary``.
    """

    raw_binary = clean_near_zero(solver_built_binary, tolerance=tolerance)
    capacities = tuple(
        clean_near_zero(value, tolerance=tolerance)
        for value in (
            energy_capacity_kWh_th,
            charge_capacity_kW_th,
            discharge_capacity_kW_th,
            power_cost_capacity_kW_th,
        )
    )
    peaks = tuple(
        clean_near_zero(value, tolerance=tolerance)
        for value in (actual_peak_charge_kW_th, actual_peak_discharge_kW_th)
    )
    used = any(value > 0.0 for value in peaks)
    installed = used or any(value > 0.0 for value in capacities)
    return TESSemantics(
        solver_built_binary=raw_binary,
        tes_installed=installed,
        tes_used=used,
        semantic_built=int(installed),
    )


__all__ = [
    "TES_RESULT_TOLERANCE",
    "TESSemantics",
    "activity_flag",
    "classify_tes_semantics",
    "clean_near_zero",
]
