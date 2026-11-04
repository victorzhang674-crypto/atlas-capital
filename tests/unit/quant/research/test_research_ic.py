"""Stage 17 Task 6 (brief task 2): information coefficient, Newey-West t, IR, hit rate, by
sector and by regime, decay across horizons and rank autocorrelation."""

import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from research_world import build_research_world

from atlas.core.errors import LookAheadError
from atlas.core.pit import tag_label
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.quant.research import (
    ICSummary,
    compound_forward,
    evaluation_dates,
    forward_returns,
    ic_by_group,
    ic_by_regime,
    ic_decay,
    ic_series,
    newey_west_tstat,
    nw_lags,
    rank_autocorrelation,
    summarize_ic,
)

DAYS = pd.DatetimeIndex(pd.bdate_range("2020-01-01", periods=400), name="date")


def _planted(
    n_dates: int, n_names: int, rho: float, seed: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(signal, labels): labels = rho * signal + sqrt(1 - rho^2) * noise, iid normal."""
    rng = np.random.default_rng(seed)
    s = rng.standard_normal((n_dates, n_names))
    e = rng.standard_normal((n_dates, n_names))
    y = rho * s + math.sqrt(1 - rho**2) * e
    cols = [f"N{i:03d}" for i in range(n_names)]
    index = DAYS[:n_dates]
    return pd.DataFrame(s, index=index, columns=cols), pd.DataFrame(
        0.02 * y, index=index, columns=cols
    )


def _spearman_of_normal(rho: float) -> float:
    return 6 / math.pi * math.asin(rho / 2)


# --- per-date IC -----------------------------------------------------------------------------


def test_ic_known_values() -> None:
    index = DAYS[:4]
    signal = pd.DataFrame(
        [[1, 2, 3, 4], [1, 2, 3, 4], [1, 2, 3, 4], [1, 2, 3, 4]],
        index=index,
        columns=list("ABCD"),
        dtype="float64",
    )
    labels = pd.DataFrame(
        [[0.1, 0.2, 0.3, 0.4], [0.4, 0.3, 0.2, 0.1], [0.1, 0.3, 0.2, 0.9], [0.5, np.nan, 0.1, 0.3]],
        index=index,
        columns=list("ABCD"),
    )
    pearson = ic_series(signal, labels, method="pearson", min_names=3)
    spearman = ic_series(signal, labels, method="spearman", min_names=3)
    assert pearson.iloc[0] == pytest.approx(1.0)
    assert pearson.iloc[1] == pytest.approx(-1.0)
    assert pearson.iloc[2] == pytest.approx(np.corrcoef([1, 2, 3, 4], [0.1, 0.3, 0.2, 0.9])[0, 1])
    assert spearman.iloc[2] == pytest.approx(0.8)  # ranks 1,3,2,4: 1 - 6*2/(4*15)
    # B's label is missing on the last date: ranks are taken over A, C, D only
    assert pearson.iloc[3] == pytest.approx(np.corrcoef([1, 3, 4], [0.5, 0.1, 0.3])[0, 1])
    assert spearman.iloc[3] == pytest.approx(np.corrcoef([1, 2, 3], [3, 1, 2])[0, 1])
    assert pearson.name == "ic_pearson" and spearman.name == "ic_spearman"
    assert list(pearson.index) == list(index)


def test_planted_ic_recovered_within_tolerance() -> None:
    # Acceptance: a planted IC of 0.05 is recovered within +-0.01 (500 names x 400 dates)
    signal, labels = _planted(400, 500, 0.05, seed=1701)
    pearson = ic_series(signal, labels, method="pearson").mean()
    spearman = ic_series(signal, labels, method="spearman").mean()
    assert abs(pearson - 0.05) <= 0.01
    assert abs(spearman - 0.05) <= 0.01
    assert spearman == pytest.approx(_spearman_of_normal(0.05), abs=0.01)


def test_planted_ic_end_to_end_through_the_lake(tmp_path: Path) -> None:
    # Acceptance: returns written to a lake so that every date's cross-sectional Pearson IC
    # of the signal with the next session's return is exactly 0.05; read back through
    # DataView (forward_returns) the toolkit recovers it.
    lake = Lake(tmp_path / "lake", registry=DEFAULT_REGISTRY.copy())
    w = build_research_world(lake, n_names=200, n_sessions=160, rho=0.05, seed=1702)
    dates = list(w.sessions[1:-2])
    labels = forward_returns(w.factory, dates, list(w.symbols), horizons=(1,))
    signal = w.signal.loc[pd.DatetimeIndex(pd.to_datetime(dates))]
    pearson = ic_series(signal, labels.horizon(1), method="pearson")
    assert pearson.notna().all()
    np.testing.assert_allclose(pearson.to_numpy(), 0.05, atol=1e-9)
    spearman = ic_series(signal, labels.horizon(1), method="spearman")
    assert abs(spearman.mean() - 0.05) <= 0.01


def test_ic_min_names_gives_nan() -> None:
    signal, labels = _planted(3, 10, 0.3, seed=3)
    labels.iloc[1, 2:] = np.nan  # only 2 names left on date 1
    out = ic_series(signal, labels, method="pearson", min_names=5)
    assert math.isnan(out.iloc[1])
    assert out.drop(out.index[1]).notna().all()
    assert ic_series(signal, labels, min_names=11).isna().all()


def test_ic_aligns_dates_and_names() -> None:
    signal, labels = _planted(5, 30, 0.3, seed=4)
    shuffled = labels[labels.columns[::-1]].iloc[::-1]
    pd.testing.assert_series_equal(ic_series(signal, labels), ic_series(signal, shuffled))
    partial = labels.iloc[:3]
    out = ic_series(signal, partial)
    assert out.iloc[3:].isna().all() and out.iloc[:3].notna().all()


def test_ic_constant_cross_section_is_nan() -> None:
    signal, labels = _planted(2, 30, 0.3, seed=5)
    signal.iloc[0] = 0.1  # its float mean is not exactly 0.1: a variance test would see ~1e-34
    for method in ("pearson", "spearman"):  # Pearson is the one rounding can fool
        out = ic_series(signal, labels, method=method)
        assert math.isnan(out.iloc[0]) and not math.isnan(out.iloc[1])


def test_ic_rejects_label_as_signal() -> None:
    _, labels = _planted(5, 30, 0.3, seed=6)
    with pytest.raises(LookAheadError, match="forward-return label"):
        ic_series(tag_label(labels), labels)
    with pytest.raises(LookAheadError):
        ic_series(tag_label(labels).shift(1) * 2, labels)


# --- Newey-West, lags, summary -------------------------------------------------------------------


def test_newey_west_known_values() -> None:
    x = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    # mean 3, gamma0 = 2, gamma1 = 0.8, gamma2 = -0.2 (each / n)
    assert newey_west_tstat(x, 0) == pytest.approx(3 / math.sqrt(2 / 5))
    s2 = 2 + 2 * (2 / 3) * 0.8 + 2 * (1 / 3) * -0.2
    assert newey_west_tstat(x, 2) == pytest.approx(3 / math.sqrt(s2 / 5))
    assert newey_west_tstat(pd.Series([1.0, np.nan, 2.0, 3.0, 4.0, 5.0]), 0) == pytest.approx(
        newey_west_tstat(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), 0)
    )
    assert math.isnan(newey_west_tstat(pd.Series([0.1]), 0))
    assert math.isnan(newey_west_tstat(pd.Series([0.1, 0.1, 0.1]), 1))  # zero variance
    with pytest.raises(ValueError, match="lags"):
        newey_west_tstat(x, -1)


def test_newey_west_widens_for_overlapping_windows() -> None:
    rng = np.random.default_rng(7)
    e = rng.standard_normal(2000)
    overlapping = pd.Series(np.convolve(e, np.ones(10), mode="valid") / 10 + 0.05)
    naive = newey_west_tstat(overlapping, 0)
    corrected = newey_west_tstat(overlapping, 9)
    assert corrected < naive / 2  # 10-period overlap inflates the naive t about 3x


def test_nw_lags_from_horizon_and_spacing() -> None:
    monthly = evaluation_dates(date(2018, 1, 1), date(2019, 12, 31), "monthly")
    assert nw_lags(1, monthly) == 0
    assert nw_lags(21, monthly) == 0
    assert nw_lags(63, monthly) == 2
    daily = evaluation_dates(date(2018, 1, 1), date(2018, 3, 31), "daily")
    assert nw_lags(1, daily) == 0
    assert nw_lags(5, daily) == 4
    weekly = evaluation_dates(date(2018, 1, 1), date(2018, 6, 30), "weekly")
    assert nw_lags(21, weekly) == 4
    assert nw_lags(5, [date(2018, 1, 2)]) == 4  # one date: spacing 1 session


def test_summarize_ic_fields() -> None:
    series = pd.Series([0.1, -0.05, 0.2, 0.0, np.nan])
    out = summarize_ic(series, lags=0)
    assert isinstance(out, ICSummary)
    valid = np.array([0.1, -0.05, 0.2, 0.0])
    assert out.n_dates == 4
    assert out.mean == pytest.approx(valid.mean())
    assert out.std == pytest.approx(valid.std(ddof=1))
    assert out.ir == pytest.approx(valid.mean() / valid.std(ddof=1))
    assert out.t_stat == pytest.approx(newey_west_tstat(series, 0))
    assert out.hit_rate == pytest.approx(2 / 4)  # an IC of exactly 0 is not a hit
    assert out.lags == 0
    assert out.to_dict() == {
        "mean": out.mean, "std": out.std, "ir": out.ir, "t_stat": out.t_stat,
        "lags": 0, "hit_rate": out.hit_rate, "n_dates": 4,
    }  # fmt: skip
    empty = summarize_ic(pd.Series([np.nan, np.nan]), lags=1)
    assert empty.n_dates == 0 and math.isnan(empty.mean) and math.isnan(empty.hit_rate)


# --- by sector, by regime ----------------------------------------------------------------------


def test_ic_by_sector_excludes_names_without_sector() -> None:
    rng = np.random.default_rng(8)
    n_dates, n = 60, 90
    s = rng.standard_normal((n_dates, n))
    y = rng.standard_normal((n_dates, n))
    cols = [f"N{i:03d}" for i in range(n)]
    sectors = pd.Series([("A", "B", None)[i % 3] for i in range(n)], index=cols, dtype="object")
    a = np.array([g == "A" for g in sectors])
    b = np.array([g == "B" for g in sectors])
    y[:, a] = 0.6 * s[:, a] + 0.8 * y[:, a]
    y[:, b] = -0.6 * s[:, b] + 0.8 * y[:, b]
    signal = pd.DataFrame(s, index=DAYS[:n_dates], columns=cols)
    labels = pd.DataFrame(y, index=DAYS[:n_dates], columns=cols)
    out = ic_by_group(signal, labels, sectors, lags=0, method="pearson", min_names=10)
    assert list(out.index) == ["A", "B"]
    assert out.loc["A", "mean"] > 0.45 and out.loc["B", "mean"] < -0.45
    assert out.loc["A", "names"] == 30 and out.loc["A", "n_dates"] == n_dates
    assert set(out.columns) == {
        "mean",
        "std",
        "ir",
        "t_stat",
        "lags",
        "hit_rate",
        "n_dates",
        "names",
    }
    small = ic_by_group(signal, labels, sectors, lags=0, method="pearson", min_names=31)
    assert (small["n_dates"] == 0).all()


def test_ic_by_regime_groups_dates() -> None:
    series = pd.Series([0.1, 0.2, -0.1, -0.3, 0.05], index=DAYS[:5])
    regimes = pd.Series(["bull", "bull", "bear", "bear", None], index=DAYS[:5], dtype="object")
    out = ic_by_regime(series, regimes, lags=0)
    assert list(out.index) == ["bear", "bull"]
    assert out.loc["bull", "mean"] == pytest.approx(0.15)
    assert out.loc["bear", "mean"] == pytest.approx(-0.2)
    assert out.loc["bear", "hit_rate"] == 0.0
    assert out.loc["bull", "n_dates"] == 2


# --- decay ---------------------------------------------------------------------------------------


def test_ic_decay_across_horizons() -> None:
    # The signal predicts only the next session's return: IC(h) ~ rho / sqrt(h).
    rng = np.random.default_rng(9)
    n_s, n, rho = 320, 300, 0.3
    s = rng.standard_normal((n_s, n))
    r = np.full((n_s, n), np.nan)
    for t in range(n_s - 1):
        r[t + 1] = 0.01 * (rho * s[t] + math.sqrt(1 - rho**2) * rng.standard_normal(n))
    cols = [f"N{i:03d}" for i in range(n)]
    daily = pd.DataFrame(r, index=DAYS[:n_s], columns=cols)
    signal = pd.DataFrame(s, index=DAYS[:n_s], columns=cols).iloc[:280]
    labels = {h: compound_forward(daily, list(signal.index), h) for h in (1, 4, 16)}
    out = ic_decay(signal, labels, method="pearson")
    assert list(out.index) == [1, 4, 16]
    for h in (1, 4, 16):
        assert out[h] == pytest.approx(rho / math.sqrt(h), abs=0.02)
    assert out[1] > out[4] > out[16]


def test_rank_autocorrelation_known() -> None:
    rng = np.random.default_rng(10)
    n_dates, n = 200, 400
    cols = [f"N{i:03d}" for i in range(n)]
    constant = pd.DataFrame(
        np.tile(rng.standard_normal(n), (n_dates, 1)), index=DAYS[:n_dates], columns=cols
    )
    out = rank_autocorrelation(constant, (1, 3))
    assert out.loc[1] == pytest.approx(1.0) and out.loc[3] == pytest.approx(1.0)
    independent = pd.DataFrame(
        rng.standard_normal((n_dates, n)), index=DAYS[:n_dates], columns=cols
    )
    assert abs(rank_autocorrelation(independent, (1,)).loc[1]) < 0.02
    phi = 0.8
    ar = np.empty((n_dates, n))
    ar[0] = rng.standard_normal(n)
    for t in range(1, n_dates):
        ar[t] = phi * ar[t - 1] + math.sqrt(1 - phi**2) * rng.standard_normal(n)
    persistent = pd.DataFrame(ar, index=DAYS[:n_dates], columns=cols)
    out = rank_autocorrelation(persistent, (1, 2))
    assert out.loc[1] == pytest.approx(_spearman_of_normal(phi), abs=0.02)
    assert out.loc[2] == pytest.approx(_spearman_of_normal(phi**2), abs=0.02)
    assert list(out.index) == [1, 2] and out.index.name == "lag"
    with pytest.raises(LookAheadError):
        rank_autocorrelation(tag_label(persistent), (1,))
