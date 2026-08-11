"""单位保持为 kW、时间稳定映射为 hour=1...N 的回归测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from clustering import cluster_heat_demand
from competition.adapters.load_timeseries import (
    LoadTimeContractError,
    adapt_standard_hourly_loads,
    validate_legacy_hour_index,
)
from hd_time_series_generator import (
    check_yearly_demand_deviation,
    convert_dwelling_TS_to_building_TS,
)


def _standard_loads() -> pd.DataFrame:
    timestamps = pd.date_range(
        "2026-01-01T00:00:00", periods=24, freq="h", tz="Asia/Shanghai"
    )
    buildings = ("building_0", "building_1", "building_2", "building_3")
    base_loads = (10.25, 20.5, 30.75, 40.125)
    rows = []
    for hour_offset, timestamp in enumerate(timestamps):
        for building_id, base_load in zip(buildings, base_loads, strict=True):
            rows.append(
                {
                    "timestamp": timestamp,
                    "building_id": building_id,
                    "heating_kW": base_load + hour_offset / 100.0,
                }
            )
    return pd.DataFrame(rows)


def test_standard_loads_keep_float_kw_and_map_to_one_based_hours() -> None:
    source = _standard_loads()
    source_before = source.copy(deep=True)

    adapted = adapt_standard_hourly_loads(source)

    pd.testing.assert_frame_equal(source, source_before)
    assert adapted.timestamp_hour_map["hour"].tolist() == list(range(1, 25))
    assert adapted.legacy_wide_kW["hour"].tolist() == list(range(1, 25))
    assert all(
        adapted.legacy_wide_kW[column].dtype == np.dtype("float64")
        for column in adapted.legacy_wide_kW.columns[1:]
    )
    assert adapted.legacy_wide_kW.iloc[:, 1:].to_numpy().sum() == pytest.approx(
        source["heating_kW"].sum(), rel=0, abs=1e-12
    )


def test_generator_preserves_fractional_kw_without_thousand_scaling() -> None:
    dwelling_kw = np.array(
        [
            [0.10, 0.20, 1.25, 2.50],
            [0.15, 0.25, 1.50, 2.75],
        ],
        dtype=np.float64,
    )
    building_ids = np.array([0, 0, 1, 1])

    building_kw = convert_dwelling_TS_to_building_TS(dwelling_kw, building_ids)

    assert building_kw["hour"].tolist() == [1, 2]
    assert building_kw["building_0"].tolist() == pytest.approx([0.30, 0.40])
    assert building_kw["building_1"].tolist() == pytest.approx([3.75, 4.25])
    assert building_kw["building_0"].dtype == np.dtype("float64")


def test_yearly_kwh_reconciliation_uses_kw_times_one_hour(capsys: pytest.CaptureFixture[str]) -> None:
    buildings = pd.DataFrame({"YearlyDemand": [3.0, 7.0]})
    hourly_kw = pd.DataFrame(
        {
            "hour": [1, 2],
            "building_0": [1.0, 2.0],
            "building_1": [3.0, 4.0],
        }
    )

    check_yearly_demand_deviation(buildings, hourly_kw)

    assert "Warning" not in capsys.readouterr().out


def test_cluster_aggregation_is_exact_kw_sum() -> None:
    adapted = adapt_standard_hourly_loads(_standard_loads())
    nodes = pd.DataFrame(
        {
            "cluster_id": ["cluster_1", "cluster_2", "site_1"],
            "building_id": [
                ["building_0", "building_1"],
                ["building_2", "building_3"],
                np.nan,
            ],
        }
    )

    clustered = cluster_heat_demand(nodes, adapted.legacy_wide_kW)

    original_total = adapted.legacy_wide_kW.iloc[:, 1:].to_numpy().sum()
    clustered_total = clustered[["cluster_1", "cluster_2"]].to_numpy().sum()
    assert clustered_total == pytest.approx(original_total, rel=0, abs=1e-12)
    assert clustered["site_1"].eq(0.0).all()


@pytest.mark.parametrize(
    "hours",
    [
        [0, 1, 2],
        [1, 3, 4],
        [1, 2, 2],
        [1, 3, 2],
    ],
)
def test_legacy_hour_index_rejects_zero_gap_duplicate_and_disorder(
    hours: list[int],
) -> None:
    with pytest.raises(LoadTimeContractError, match="1...N"):
        validate_legacy_hour_index(pd.DataFrame({"hour": hours, "building_0": 1.0}))


def test_standard_loads_reject_naive_timestamp() -> None:
    invalid = _standard_loads()
    invalid["timestamp"] = invalid["timestamp"].dt.tz_localize(None)
    with pytest.raises(LoadTimeContractError, match="带时区"):
        adapt_standard_hourly_loads(invalid)


def test_standard_loads_reject_wrong_timezone() -> None:
    invalid = _standard_loads()
    invalid["timestamp"] = invalid["timestamp"].dt.tz_convert("UTC")
    with pytest.raises(LoadTimeContractError, match="Asia/Shanghai"):
        adapt_standard_hourly_loads(invalid)


def test_standard_loads_reject_non_hour_boundary() -> None:
    invalid = _standard_loads()
    invalid["timestamp"] = invalid["timestamp"] + pd.Timedelta(minutes=30)
    with pytest.raises(LoadTimeContractError, match="整点"):
        adapt_standard_hourly_loads(invalid)


@pytest.mark.parametrize("invalid_value", [-1.0, np.nan, np.inf, -np.inf])
def test_standard_loads_reject_negative_or_nonfinite_kw(invalid_value: float) -> None:
    invalid = _standard_loads()
    invalid.loc[0, "heating_kW"] = invalid_value
    with pytest.raises(LoadTimeContractError):
        adapt_standard_hourly_loads(invalid)


def test_standard_loads_reject_duplicate_key() -> None:
    invalid = pd.concat([_standard_loads(), _standard_loads().iloc[[0]]], ignore_index=True)
    with pytest.raises(LoadTimeContractError, match="不得重复"):
        adapt_standard_hourly_loads(invalid)


def test_standard_loads_reject_disordered_rows() -> None:
    invalid = _standard_loads()
    reordered = list(range(len(invalid)))
    reordered[0], reordered[1] = reordered[1], reordered[0]
    invalid = invalid.iloc[reordered].reset_index(drop=True)
    with pytest.raises(LoadTimeContractError, match="升序"):
        adapt_standard_hourly_loads(invalid)


def test_standard_loads_reject_hour_gap() -> None:
    invalid = _standard_loads()
    missing_hour = invalid["timestamp"].drop_duplicates().iloc[12]
    invalid = invalid.loc[invalid["timestamp"] != missing_hour].reset_index(drop=True)
    with pytest.raises(LoadTimeContractError, match="严格连续"):
        adapt_standard_hourly_loads(invalid)


def test_standard_loads_reject_cross_building_coverage_mismatch() -> None:
    invalid = _standard_loads().drop(index=0).reset_index(drop=True)
    with pytest.raises(LoadTimeContractError, match="完全相同"):
        adapt_standard_hourly_loads(invalid)


def test_legacy_wide_rejects_timestamp_and_hour_mixing() -> None:
    invalid = pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2026-01-01", periods=2, freq="h", tz="Asia/Shanghai"
            ),
            "hour": [1, 2],
            "building_0": [1.0, 2.0],
        }
    )
    with pytest.raises(LoadTimeContractError, match="不能同时包含"):
        validate_legacy_hour_index(invalid)


def test_legacy_wide_rejects_float_hour_dtype() -> None:
    with pytest.raises(LoadTimeContractError, match="整数列"):
        validate_legacy_hour_index(
            pd.DataFrame({"hour": [1.0, 2.0], "building_0": [1.0, 2.0]})
        )
