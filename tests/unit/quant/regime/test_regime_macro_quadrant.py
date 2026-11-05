"""Stage 21 Task 4: macro quadrant, credit proxy / z-score and the stress flag."""

import numpy as np
import pandas as pd
import pytest

from atlas.core.config import RegimeConfig, RegimeCreditConfig
from atlas.quant.regime.macro_quadrant import (
    QUADRANTS,
    acceleration,
    claims_trend,
    classify_quadrant,
    credit_proxy,
    credit_zscore,
    growth_votes,
    inflation_gap,
    stress_flag,
    trailing_zscore,
)

CFG = RegimeConfig()


def _monthly(values: list[float], start: str = "2018-01-01") -> pd.Series:
    return pd.Series(
        values, index=pd.date_range(start, periods=len(values), freq="MS"), dtype=float
    )


def _weekly(values: list[float]) -> pd.Series:
    return pd.Series(
        values, index=pd.date_range("2019-01-05", periods=len(values), freq="7D"), dtype=float
    )


# --- growth and inflation ----------------------------------------------------------------------


def test_acceleration_is_3m_annualised_minus_yoy() -> None:
    # +1% a month for 12 months then +2% a month: accelerating
    vals = [100.0 * 1.01**i for i in range(13)]
    vals += [vals[-1] * 1.02**i for i in range(1, 4)]
    s = _monthly(vals)
    x = s.iloc[-1]
    ann3 = 100 * ((x / s.iloc[-4]) ** 4 - 1)
    yoy = 100 * (x / s.iloc[-13] - 1)
    assert acceleration(s) == pytest.approx(ann3 - yoy)
    assert acceleration(s) > 0
    assert np.isnan(acceleration(_monthly([1.0, 2.0, 3.0])))  # no year-ago value


def test_acceleration_ignores_missing_rows() -> None:
    s = _monthly([100.0 * 1.01**i for i in range(16)])
    with_gaps = s.copy()
    with_gaps.iloc[[2, 5]] = np.nan  # other series' rows in a joint frame
    assert acceleration(with_gaps.dropna()) == pytest.approx(acceleration(with_gaps))


def test_claims_trend() -> None:
    claims = _weekly([200.0] * 22 + [260.0] * 4)
    assert claims_trend(claims, 4, 26) == pytest.approx(260.0 - (200 * 22 + 260 * 4) / 26)
    assert np.isnan(claims_trend(claims.iloc[:10], 4, 26))


def test_growth_votes_signs_and_missing() -> None:
    accel = _monthly([100.0 * 1.01**i for i in range(13)] + [113 * 1.03**i for i in range(1, 4)])
    decel = _monthly([100.0 * 1.02**i for i in range(13)] + [129 * 1.001**i for i in range(1, 4)])
    rising_claims = _weekly([200.0] * 22 + [300.0] * 4)
    votes = growth_votes(accel, decel, rising_claims, CFG.macro)
    assert votes == {"INDPRO": 1.0, "PAYEMS": -1.0, "ICSA": -1.0}
    votes = growth_votes(accel, None, _weekly([300.0] * 22 + [200.0] * 4), CFG.macro)
    assert votes["INDPRO"] == 1.0 and np.isnan(votes["PAYEMS"]) and votes["ICSA"] == 1.0


def test_inflation_gap() -> None:
    hot = _monthly([100.0 * 1.002**i for i in range(13)] + [102.4 * 1.006**i for i in range(1, 4)])
    assert inflation_gap(hot) > 0
    assert np.isnan(inflation_gap(None))


@pytest.mark.parametrize(
    ("growth", "inflation", "quadrant"),
    [
        (1.0, -0.5, "Goldilocks"),
        (1 / 3, 0.2, "Reflation"),
        (-1.0, 0.2, "Stagflation"),
        (-1 / 3, -0.1, "Deflation"),
        (0.0, -0.1, "Deflation"),  # a growth tie counts as down (fail safe)
        (0.0, 0.0, "Stagflation"),  # an inflation tie counts as rising (fail safe)
        (1.0, 0.0, "Reflation"),
        (np.nan, 0.1, None),
        (1.0, np.nan, None),
    ],
)
def test_classify_quadrant(growth: float, inflation: float, quadrant: str | None) -> None:
    assert classify_quadrant(growth, inflation) == quadrant


def test_quadrant_names() -> None:
    assert QUADRANTS == ("Goldilocks", "Reflation", "Stagflation", "Deflation")


# --- credit -----------------------------------------------------------------------------------


def test_credit_proxy_hand_example() -> None:
    idx = pd.bdate_range("2020-01-01", periods=3)
    hyg = pd.Series([100.0, 99.0, 99.5], index=idx)
    ief = pd.Series([100.0, 101.0, 100.0], index=idx)
    out = credit_proxy(hyg, ief, 0.5)
    x1 = np.log(99 / 100) - 0.5 * np.log(101 / 100)
    x2 = np.log(99.5 / 99) - 0.5 * np.log(100 / 101)
    np.testing.assert_allclose(out["change"].to_numpy(), [np.nan, -x1, -x2])
    np.testing.assert_allclose(out["level"].to_numpy(), [0.0, -x1, -x1 - x2])


def test_credit_proxy_gap_leaves_change_missing_and_level_flat() -> None:
    idx = pd.bdate_range("2020-01-01", periods=4)
    hyg = pd.Series([100.0, np.nan, 99.0, 98.0], index=idx)
    ief = pd.Series([100.0, 100.0, 100.0, 100.0], index=idx)
    out = credit_proxy(hyg, ief, 0.5)
    assert np.isnan(out["change"].iloc[1]) and np.isnan(out["change"].iloc[2])
    assert out["level"].iloc[3] == pytest.approx(-np.log(98 / 99))


def test_trailing_zscore() -> None:
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 10.0], index=pd.bdate_range("2020-01-01", periods=5))
    z = trailing_zscore(s, 3)
    w = np.array([3.0, 4.0, 10.0])
    assert z.iloc[-1] == pytest.approx((10 - w.mean()) / w.std(ddof=1))
    assert z.iloc[:2].isna().all()
    pd.testing.assert_series_equal(trailing_zscore(s.iloc[:4], 3), z.iloc[:4])  # PIT


def test_credit_zscore_source_selection() -> None:
    idx = pd.bdate_range("2019-01-01", periods=300)
    rng = np.random.default_rng(0)
    proxy = pd.Series(np.cumsum(rng.normal(size=300)), index=idx)
    oas_short = pd.Series(4.0 + rng.normal(size=100) * 0.1, index=idx[-100:])
    oas_long = pd.Series(4.0 + rng.normal(size=300) * 0.1, index=idx)
    cfg = RegimeCreditConfig(zscore_window=252)
    z, src = credit_zscore(oas_short, proxy, cfg)
    assert src == "proxy" and z == pytest.approx(trailing_zscore(proxy, 252).iloc[-1])
    z, src = credit_zscore(oas_long, proxy, cfg)
    assert src == "oas" and z == pytest.approx(trailing_zscore(oas_long, 252).iloc[-1])
    z, src = credit_zscore(None, proxy, cfg)
    assert src == "proxy"
    z, src = credit_zscore(oas_short, proxy, RegimeCreditConfig(source="oas"))
    assert src == "oas" and np.isnan(z)
    z, src = credit_zscore(oas_long, proxy, RegimeCreditConfig(source="proxy"))
    assert src == "proxy"
    z, src = credit_zscore(None, None, cfg)
    assert src is None and np.isnan(z)


# --- stress -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("z", "nfci", "flag", "missing"),
    [
        (2.5, -0.5, True, ()),
        (2.0, -0.5, False, ()),  # strictly above the threshold
        (0.0, 0.1, True, ()),
        (0.0, 0.0, False, ()),
        (np.nan, 0.2, True, ("credit",)),
        (3.0, np.nan, True, ("nfci",)),
        (np.nan, np.nan, False, ("credit", "nfci")),
    ],
)
def test_stress_flag(z: float, nfci: float, flag: bool, missing: tuple[str, ...]) -> None:
    assert stress_flag(z, nfci, CFG) == (flag, missing)


def test_credit_proxy_level_starts_with_the_data() -> None:
    """Sessions before the legs have prices have no level (not a flat 0 that would enter the
    z-score window)."""
    idx = pd.bdate_range("2020-01-01", periods=5)
    hyg = pd.Series([np.nan, np.nan, 100.0, 101.0, 100.0], index=idx)
    ief = pd.Series([np.nan, 100.0, 100.0, 100.0, 100.0], index=idx)
    out = credit_proxy(hyg, ief, 0.5)
    assert out["level"].iloc[:2].isna().all()
    assert out["level"].iloc[2] == 0.0
    assert out["level"].iloc[4] == pytest.approx(-np.log(101 / 100) - np.log(100 / 101))
