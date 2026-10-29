"""Stage 10 Task 6: derived macro transforms with known values."""

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from macro_helpers import CPI, DGS10, stored_rows

from atlas.core.config import MacroConfig
from atlas.core.pit import AsOf
from atlas.data.macro import (
    annualized_3m_pct,
    curve_slope,
    macro_pit,
    real_rate,
    yoy_pct,
    zscore,
)

LONG = timedelta(days=4000)


def monthly(start: str, values: list[float]) -> pd.Series:
    index = pd.date_range(start, periods=len(values), freq="MS", name="obs_date")
    return pd.Series(values, index=index, dtype="float64")


def test_yoy_pct_cpi_first_release() -> None:
    frame = macro_pit(stored_rows(CPI), ["CPIAUCSL"], AsOf.for_session(date(2020, 4, 15)), LONG)
    yoy = yoy_pct(frame["CPIAUCSL"])
    assert yoy[pd.Timestamp("2020-03-01")] == pytest.approx(100 * (257.953 / 254.095 - 1))
    assert yoy[pd.Timestamp("2020-03-01")] == pytest.approx(1.51833, abs=1e-5)
    assert math.isnan(yoy.iloc[0])  # no year-ago value inside the frame


def test_yoy_needs_the_year_ago_value() -> None:
    s = monthly("2019-01-01", [100.0 + i for i in range(14)]).drop(pd.Timestamp("2019-02-01"))
    yoy = yoy_pct(s)
    assert math.isnan(yoy[pd.Timestamp("2020-02-01")])  # 2019-02 missing; Jan is 31 days early
    assert yoy[pd.Timestamp("2020-01-01")] == pytest.approx(12.0)


def test_yoy_daily_uses_previous_business_day() -> None:
    index = pd.bdate_range("2019-03-01", "2020-03-02", name="obs_date")
    s = pd.Series(np.linspace(100.0, 110.0, len(index)), index=index)
    yoy = yoy_pct(s)
    assert yoy[pd.Timestamp("2020-03-02")] == pytest.approx(10.0)  # vs Fri 2019-03-01
    strict = yoy_pct(s, MacroConfig(transform_lag_tolerance_days=0))
    assert math.isnan(strict[pd.Timestamp("2020-03-02")])  # 2019-03-02 is a Saturday


def test_annualized_3m_pct() -> None:
    out = annualized_3m_pct(monthly("2020-01-01", [100.0, 101.0, 102.0, 103.0]))
    assert out.iloc[:3].isna().all()
    assert out.iloc[3] == pytest.approx(100 * (1.03**4 - 1))
    assert out.iloc[3] == pytest.approx(12.550881, abs=1e-6)


def test_zscore_known_value() -> None:
    s = monthly("2000-01-01", [float(i) for i in range(156)])
    z = zscore(s)
    t = pd.Timestamp("2012-12-01")
    window = s[s.index > t - pd.Timedelta(days=3652)]
    expected = (s[t] - window.mean()) / window.std(ddof=1)
    assert z[t] == pytest.approx(expected)
    assert z[pd.Timestamp("2004-12-01")] != z[pd.Timestamp("2004-12-01")]  # NaN: < 5 years
    assert not math.isnan(z[pd.Timestamp("2005-01-01")])
    flat = zscore(monthly("2000-01-01", [1.0] * 80))
    assert flat.isna().all()  # zero dispersion: no z-score


def test_curve_slope() -> None:
    d = pd.DatetimeIndex(["2020-03-02", "2020-03-03", "2020-03-04"], name="obs_date")
    long = pd.Series([1.10, 1.02, 1.02], index=d)
    short = pd.Series([0.89, 0.80], index=d[:2])
    slope = curve_slope(long, short)
    assert slope.index.tolist() == list(d[:2])
    assert slope.tolist() == pytest.approx([0.21, 0.22])


def test_real_rate_uses_latest_inflation() -> None:
    frame = macro_pit(stored_rows(DGS10), ["DGS10"], AsOf.for_session(date(2020, 5, 4)), LONG)
    inflation = monthly("2020-03-01", [1.8, 1.0])
    real = real_rate(frame["DGS10"], inflation)
    assert real[pd.Timestamp("2020-03-02")] == pytest.approx(1.10 - 1.8)
    assert real[pd.Timestamp("2020-04-15")] == pytest.approx(0.63 - 1.0)
    early = real_rate(frame["DGS10"], monthly("2020-04-01", [1.0]))
    assert math.isnan(early[pd.Timestamp("2020-03-31")])  # no inflation known for March yet


def test_transforms_reject_unsorted_or_non_datetime_index() -> None:
    s = pd.Series([1.0, 2.0], index=pd.DatetimeIndex(["2020-02-01", "2020-01-01"]))
    with pytest.raises(ValueError, match="ascending"):
        yoy_pct(s)
    with pytest.raises(ValueError, match="DatetimeIndex"):
        zscore(pd.Series([1.0], index=[date(2020, 1, 1)]))
