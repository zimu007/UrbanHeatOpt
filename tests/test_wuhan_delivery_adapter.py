from __future__ import annotations

from pathlib import Path

import pandas as pd

from competition.adapters.wuhan_delivery import _adapt_loads


def test_load_adapter_scales_unit_area_load_and_sorts_standard_rows(tmp_path: Path) -> None:
    folder = tmp_path / "Wuhan_DeST_models汇总" / "CoA_Wuhan_2015" / "006_building_load"
    folder.mkdir(parents=True)
    values = [1.0] * 8760
    values[1] = float("nan")
    values[2] = -0.0005
    pd.DataFrame({
        "日期": ["01-01"] * 8760,
        "小时": range(8760),
        "热负荷（W/m2）": values,
        "冷负荷（W/m2）": [0.0] * 8760,
        "加湿量（g/h/m2）": [0.0] * 8760,
    }).to_csv(folder / "006_building_load1_建筑逐时单位面积负荷.csv", index=False)
    buildings = pd.DataFrame([
        {"building_id": "b2", "area_m2": 2000.0, "prototype_type": "COA_2015"},
        {"building_id": "b1", "area_m2": 1000.0, "prototype_type": "COA_2015"},
    ])

    loads, reconciliation = _adapt_loads(tmp_path, buildings, 2025, "test-v1")

    assert loads.iloc[:2]["building_id"].tolist() == ["b1", "b2"]
    assert loads["heating_kW"].min() == 0.0
    assert len(loads) == 2 * 8760
    assert reconciliation["relative_error"].max() == 0.0
    assert reconciliation["filled_null_count"].tolist() == [1, 1]
    assert reconciliation["clipped_small_negative_count"].tolist() == [1, 1]
