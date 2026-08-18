from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from competition.adapters.legacy_case import adapt_case_to_legacy
from competition.costing.annualized import (
    annualize_capex,
    capital_recovery_factor,
    connection_annualized_capex,
    electricity_cost,
    gas_cost,
    pipe_annualized_capex,
    variable_om_cost,
)
from scripts.create_minimal_case import create_minimal_case


def test_capital_recovery_factor_matches_manual_formula() -> None:
    discount_rate = 0.05
    lifetime_years = 20
    factor = (1 + discount_rate) ** lifetime_years
    expected = discount_rate * factor / (factor - 1)

    assert capital_recovery_factor(discount_rate, lifetime_years) == pytest.approx(expected)
    assert capital_recovery_factor(0.0, lifetime_years) == pytest.approx(1 / lifetime_years)


def test_annualize_capex_respects_cost_basis() -> None:
    capex = 1000.0
    expected = capex * capital_recovery_factor(0.05, 20)

    assert annualize_capex(capex, 0.05, 20, "one_time_capex") == pytest.approx(expected)
    assert annualize_capex(capex, 0.05, 20, "annualized") == pytest.approx(capex)


def test_energy_cost_formulas_match_manual_calculation() -> None:
    assert electricity_cost(heat_output_kW=120, cop=3, price_CNY_per_kWh=1.2, timestep_h=2, weight=4) == pytest.approx(
        120 / 3 * 1.2 * 2 * 4
    )
    assert gas_cost(
        heat_output_kW=90,
        efficiency=0.9,
        gas_price_CNY_per_Nm3=3.54,
        gas_lhv_kWh_per_Nm3=9.8,
        timestep_h=2,
        weight=3,
    ) == pytest.approx(90 * 2 / 0.9 / 9.8 * 3.54 * 3)
    assert variable_om_cost(heat_output_kW=50, variable_om_CNY_per_kWh_heat=0.03, timestep_h=2, weight=5) == pytest.approx(
        50 * 0.03 * 2 * 5
    )


def test_pipe_and_connection_costs_are_zero_when_not_built_or_connected() -> None:
    assert pipe_annualized_capex(0, 100, 500, 0.05, 30, "one_time_capex") == pytest.approx(0)
    assert connection_annualized_capex(0, 10, 1000, 500, 100, 2, 50, 0.05, 20, "one_time_capex") == pytest.approx(0)


def test_legacy_adapter_annualizes_one_time_power_capex(tmp_path: Path) -> None:
    case_dir = create_minimal_case(tmp_path / "case")
    result = adapt_case_to_legacy(case_dir, tmp_path / "legacy")

    heat_units = pd.read_excel(result.heat_generation_units_xlsx)
    annualized_cost = float(heat_units.loc[1, "Power Investment Costs"])
    expected = 10.0 * capital_recovery_factor(0.05, 20)

    assert annualized_cost == pytest.approx(expected)
