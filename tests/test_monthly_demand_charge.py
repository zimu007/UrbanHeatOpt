"""Hand calculations for the virtual-meter monthly demand charge; no solver."""
from __future__ import annotations

import pytest

from urbanheatopt.model.costing.annualized import monthly_demand_charge


RATE = 42.0


def charge(power, months, rate=RATE):
    return monthly_demand_charge(power, months, rate)


def test_single_month_uses_coincident_total_peak():
    peaks, fee = charge({1: 100, 2: 150}, {1: "2025-12", 2: "2025-12"})
    assert peaks == {"2025-12": 150}
    assert fee == 150 * RATE


def test_staggered_devices_are_summed_by_hour_before_peak():
    central, local = {1: 100, 2: 0}, {1: 0, 2: 120}
    total = {h: central[h] + local[h] for h in central}
    peaks, fee = charge(total, {1: "2025-12", 2: "2025-12"})
    assert peaks["2025-12"] == 120
    assert fee == 120 * RATE
    assert peaks["2025-12"] != max(central.values()) + max(local.values())


def test_same_hour_devices_add_at_virtual_meter():
    peaks, _ = charge({1: 100 + 120}, {1: "2025-12"})
    assert peaks["2025-12"] == 220


def test_circulation_pump_increases_coincident_peak():
    base = charge({1: 220}, {1: "2025-12"})[0]["2025-12"]
    with_pump = charge({1: 220 + 15}, {1: "2025-12"})[0]["2025-12"]
    assert with_pump - base == 15


def test_three_months_are_independently_billed():
    peaks, fee = charge(
        {1: 100, 2: 150, 3: 80},
        {1: "2025-12", 2: "2026-01", 3: "2026-02"},
    )
    assert peaks == {"2025-12": 100, "2026-01": 150, "2026-02": 80}
    assert fee == (100 + 150 + 80) * RATE == 13860


def test_energy_changes_below_same_peak_do_not_change_charge():
    months = {1: "2025-12", 2: "2025-12", 3: "2025-12"}
    assert charge({1: 150, 2: 20, 3: 20}, months)[1] == charge({1: 150, 2: 140, 3: 140}, months)[1]


@pytest.mark.parametrize("months", ({1: "2025-12"}, {1: "2025-12", 2: "bad"}, {1: "2025-12", 2: "2026-13"}))
def test_missing_or_invalid_billing_month_fails_closed(months):
    with pytest.raises(ValueError, match="billing|month"):
        charge({1: 10, 2: 20}, months)


def test_zero_rate_keeps_valid_structure_and_zero_charge():
    peaks, fee = charge({1: 150}, {1: "2025-12"}, 0)
    assert peaks == {"2025-12": 150}
    assert fee == 0
