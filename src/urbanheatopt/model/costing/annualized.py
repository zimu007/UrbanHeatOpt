"""Cost formulas used by the competition layer.

The legacy UrbanHeatOpt model accepts already model-ready cost coefficients.
These helpers keep the competition cost convention explicit before data is
adapted into that legacy interface.
"""

from __future__ import annotations


def capital_recovery_factor(discount_rate: float, lifetime_years: float) -> float:
    """Return the capital recovery factor for a lifetime in years."""

    if lifetime_years <= 0:
        raise ValueError("lifetime_years must be positive")
    if discount_rate < 0:
        raise ValueError("discount_rate must be non-negative")
    if discount_rate == 0:
        return 1.0 / lifetime_years
    factor = (1.0 + discount_rate) ** lifetime_years
    return discount_rate * factor / (factor - 1.0)


def annualize_capex(capex: float, discount_rate: float, lifetime_years: float, capex_basis: str) -> float:
    """Convert a capital cost to an annualized cost if needed."""

    if capex < 0:
        raise ValueError("capex must be non-negative")
    if capex_basis == "annualized":
        return capex
    if capex_basis == "one_time_capex":
        return capex * capital_recovery_factor(discount_rate, lifetime_years)
    raise ValueError("capex_basis must be one_time_capex or annualized")


def electricity_cost(
    heat_output_kW: float,
    cop: float,
    price_CNY_per_kWh: float,
    timestep_h: float = 1.0,
    weight: float = 1.0,
) -> float:
    """Return heat-pump electricity cost for one timestep."""

    if cop <= 0:
        raise ValueError("cop must be positive")
    _raise_if_negative(
        heat_output_kW=heat_output_kW,
        price_CNY_per_kWh=price_CNY_per_kWh,
        timestep_h=timestep_h,
        weight=weight,
    )
    return heat_output_kW / cop * price_CNY_per_kWh * timestep_h * weight


def gas_cost(
    heat_output_kW: float,
    efficiency: float,
    gas_price_CNY_per_Nm3: float,
    gas_lhv_kWh_per_Nm3: float,
    timestep_h: float = 1.0,
    weight: float = 1.0,
) -> float:
    """Return gas-boiler fuel cost for one timestep."""

    if efficiency <= 0 or efficiency > 1:
        raise ValueError("efficiency must be in (0, 1]")
    if gas_lhv_kWh_per_Nm3 <= 0:
        raise ValueError("gas_lhv_kWh_per_Nm3 must be positive")
    _raise_if_negative(
        heat_output_kW=heat_output_kW,
        gas_price_CNY_per_Nm3=gas_price_CNY_per_Nm3,
        timestep_h=timestep_h,
        weight=weight,
    )
    gas_volume_Nm3 = heat_output_kW * timestep_h / efficiency / gas_lhv_kWh_per_Nm3
    return gas_volume_Nm3 * gas_price_CNY_per_Nm3 * weight


def pipe_annualized_capex(
    build_pipe: float,
    pipe_length_m: float,
    cost_CNY_per_m: float,
    discount_rate: float,
    lifetime_years: float,
    capex_basis: str,
) -> float:
    """Return annualized pipe cost for one candidate segment."""

    _raise_if_negative(build_pipe=build_pipe, pipe_length_m=pipe_length_m, cost_CNY_per_m=cost_CNY_per_m)
    return annualize_capex(build_pipe * pipe_length_m * cost_CNY_per_m, discount_rate, lifetime_years, capex_basis)


def connection_annualized_capex(
    connected: float,
    building_count: float,
    land_area_m2: float,
    peak_heat_kW: float,
    cost_CNY_per_building: float,
    cost_CNY_per_m2: float,
    cost_CNY_per_kW: float,
    discount_rate: float,
    lifetime_years: float,
    capex_basis: str,
) -> float:
    """Return annualized district-heating connection cost for one group."""

    _raise_if_negative(
        connected=connected,
        building_count=building_count,
        land_area_m2=land_area_m2,
        peak_heat_kW=peak_heat_kW,
        cost_CNY_per_building=cost_CNY_per_building,
        cost_CNY_per_m2=cost_CNY_per_m2,
        cost_CNY_per_kW=cost_CNY_per_kW,
    )
    capex = connected * (
        building_count * cost_CNY_per_building + land_area_m2 * cost_CNY_per_m2 + peak_heat_kW * cost_CNY_per_kW
    )
    return annualize_capex(capex, discount_rate, lifetime_years, capex_basis)


def variable_om_cost(
    heat_output_kW: float,
    variable_om_CNY_per_kWh_heat: float,
    timestep_h: float = 1.0,
    weight: float = 1.0,
) -> float:
    """Return variable operation and maintenance cost for one timestep."""

    _raise_if_negative(
        heat_output_kW=heat_output_kW,
        variable_om_CNY_per_kWh_heat=variable_om_CNY_per_kWh_heat,
        timestep_h=timestep_h,
        weight=weight,
    )
    return heat_output_kW * variable_om_CNY_per_kWh_heat * timestep_h * weight


def _raise_if_negative(**values: float) -> None:
    for name, value in values.items():
        if value < 0:
            raise ValueError(f"{name} must be non-negative")
