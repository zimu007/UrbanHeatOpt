from __future__ import annotations

import pandas as pd
import pytest

from tools.legacy_cli.build_guanggu_v01_6b_168h import gas_volume_to_lhv_energy_factors


def test_gas_volume_to_lhv_energy_conversion() -> None:
    price, carbon, lhv = gas_volume_to_lhv_energy_factors(
        price_CNY_per_Nm3=pd.Series([3.8]),
        carbon_kgCO2e_per_Nm3=pd.Series([2.184]),
        lhv_MJ_per_Nm3=38.931,
    )
    assert lhv == pytest.approx(38.931 / 3.6)
    assert price.iloc[0] == pytest.approx(3.8 / lhv)
    assert carbon.iloc[0] == pytest.approx(2.184 / lhv)


def test_gas_conversion_rejects_invalid_lhv() -> None:
    with pytest.raises(ValueError, match="positive"):
        gas_volume_to_lhv_energy_factors(
            price_CNY_per_Nm3=pd.Series([3.8]),
            carbon_kgCO2e_per_Nm3=pd.Series([2.184]),
            lhv_MJ_per_Nm3=0,
        )
