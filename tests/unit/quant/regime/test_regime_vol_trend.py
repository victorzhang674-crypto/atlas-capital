"""Stage 21 Task 1: rule-based volatility and trend regimes on synthetic series."""

import numpy as np
import pandas as pd
import pytest

from atlas.core.config import RegimeTrendConfig, RegimeVolConfig
from atlas.quant.regime.vol_trend import (
    TREND_LABELS,
    VOL_LABELS,
    breadth,
    classify_trend,
    classify_vol,
    trailing_percentile,
    trend_votes,
    vol_score,
)

VOL = RegimeVolConfig()
TREND = RegimeTrendConfig()


def _days(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2020-01-01", periods=n)


def _loop_percentile(values: np.ndarray, window: int, min_obs: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    for i in range(len(values)):
        if np.isnan(values[i]):
            continue
        win = values[max(0, i - window + 1) : i + 1]
        win = win[~np.isnan(win)]
        if len(win) >= min_obs:
            out[i] = np.mean(win <= values[i])
    return out


# --- percentiles ---------------------------------------------------------------------------


def test_trailing_percentile_hand_case() -> None:
    s = pd.Series([1.0, 3.0, 2.0, 2.0, 5.0], index=_days(5))
    got = trailing_percentile(s, window=3, min_obs=2)
    # windows: [1] -> NaN; [1,3] -> 1.0; [1,3,2] -> 2/3; [3,2,2] -> 2/3 (ties count); [2,2,5] -> 1
    np.testing.assert_allclose(got.to_numpy(), [np.nan, 1.0, 2 / 3, 2 / 3, 1.0])


def test_trailing_percentile_matches_loop_with_gaps() -> None:
    rng = np.random.default_rng(1)
    v = rng.normal(size=400)
    v[rng.choice(400, 40, replace=False)] = np.nan
    v[50:53] = 0.25  # ties
    s = pd.Series(v, index=_days(400))
    got = trailing_percentile(s, window=60, min_obs=20)
    np.testing.assert_allclose(got.to_numpy(), _loop_percentile(v, 60, 20), equal_nan=True)


def test_trailing_percentile_is_point_in_time() -> None:
    rng = np.random.default_rng(2)
    s = pd.Series(rng.normal(size=300), index=_days(300))
    full = trailing_percentile(s, 50, 10)
    cut = trailing_percentile(s.iloc[:200], 50, 10)
    pd.testing.assert_series_equal(full.iloc[:200], cut)


@pytest.mark.parametrize(("window", "min_obs"), [(0, 1), (10, 0), (10, 11)])
def test_trailing_percentile_rejects_bad_windows(window: int, min_obs: int) -> None:
    with pytest.raises(ValueError):
        trailing_percentile(pd.Series([1.0, 2.0], index=_days(2)), window, min_obs)


# --- volatility regime ----------------------------------------------------------------------


def test_vol_score_is_the_larger_rank_and_falls_back_to_realised() -> None:
    idx = _days(4)
    rv = pd.Series([0.2, 0.9, np.nan, 0.5], index=idx)
    vix = pd.Series([0.6, 0.1, np.nan, np.nan], index=idx)
    np.testing.assert_allclose(vol_score(rv, vix).to_numpy(), [0.6, 0.9, np.nan, 0.5])


@pytest.mark.parametrize(
    ("score", "vix", "rv", "label"),
    [
        (0.0, 12.0, 0.08, "LOW"),
        (0.2499, 12.0, 0.08, "LOW"),
        (0.25, 14.0, 0.10, "NORMAL"),
        (0.7499, 18.0, 0.15, "NORMAL"),
        (0.75, 22.0, 0.18, "HIGH"),
        (0.9499, 40.0, 0.40, "HIGH"),
        (0.95, 30.0, 0.10, "CRISIS"),  # VIX floor met
        (0.95, 25.0, 0.30, "CRISIS"),  # realised floor met
        (1.0, 29.9, 0.299, "HIGH"),  # neither floor: a calm-window high is not a crisis
        (1.0, np.nan, 0.35, "CRISIS"),  # VIX missing: realised floor decides
        (1.0, np.nan, np.nan, "HIGH"),
    ],
)
def test_classify_vol_boundaries(score: float, vix: float, rv: float, label: str) -> None:
    idx = _days(1)
    got = classify_vol(
        pd.Series([score], index=idx), pd.Series([vix], index=idx), pd.Series([rv], index=idx), VOL
    )
    assert got.iloc[0] == label


def test_classify_vol_missing_score_is_none() -> None:
    idx = _days(1)
    nan = pd.Series([np.nan], index=idx)
    assert classify_vol(nan, nan, nan, VOL).iloc[0] is None


def test_calm_then_spike_series() -> None:
    """A synthetic calm year then a volatility spike: LOW/NORMAL first, CRISIS in the spike."""
    rng = np.random.default_rng(3)
    n_calm, n_spike = 400, 30
    rv = pd.Series(
        np.concatenate([0.10 + 0.02 * rng.random(n_calm), np.linspace(0.3, 0.8, n_spike)]),
        index=_days(n_calm + n_spike),
    )
    vix = 100 * rv + 2
    cfg = RegimeVolConfig(percentile_window=300, percentile_min_obs=100)
    score = vol_score(trailing_percentile(rv, 300, 100), trailing_percentile(vix, 300, 100))
    labels = classify_vol(score, vix, rv, cfg)
    calm = labels.iloc[100:n_calm]
    assert set(calm) <= {"LOW", "NORMAL", "HIGH"}
    assert (calm == "CRISIS").sum() == 0
    assert (labels.iloc[n_calm:] == "CRISIS").all()
    assert labels.iloc[:99].isna().all()


def test_labels_constants() -> None:
    assert VOL_LABELS == ("LOW", "NORMAL", "HIGH", "CRISIS")
    assert TREND_LABELS == ("UP", "NEUTRAL", "DOWN")


# --- breadth --------------------------------------------------------------------------------


def test_breadth_share_of_members_with_a_value() -> None:
    idx = _days(2)
    ratio = pd.DataFrame(
        {"A": [1.1, 0.9], "B": [0.8, 1.2], "C": [1.3, np.nan], "D": [1.2, 1.4]}, index=idx
    )
    members = pd.DataFrame(
        {"A": [True, True], "B": [True, True], "C": [True, True], "D": [False, True]}, index=idx
    )
    got = breadth(ratio, members, min_names=2)
    np.testing.assert_allclose(got.to_numpy(), [2 / 3, 2 / 3])
    assert np.isnan(breadth(ratio, members, min_names=4).iloc[0])


def test_breadth_rejects_misaligned_frames() -> None:
    idx = _days(2)
    with pytest.raises(ValueError, match="aligned"):
        breadth(
            pd.DataFrame({"A": [1.0, 1.0]}, index=idx),
            pd.DataFrame({"B": [True, True]}, index=idx),
            min_names=1,
        )


# --- trend regime ---------------------------------------------------------------------------


def test_trend_votes_values() -> None:
    idx = _days(3)
    votes = trend_votes(
        pd.Series([1.05, 0.95, np.nan], index=idx),
        pd.Series([0.1, -0.2, 0.0], index=idx),
        pd.Series([0.6, 0.5, np.nan], index=idx),
        TREND,
    )
    assert list(votes.columns) == ["ma", "momentum", "breadth"]
    np.testing.assert_allclose(
        votes.to_numpy(), [[1, 1, 1], [0, 0, 0], [np.nan, 0, np.nan]], equal_nan=True
    )


@pytest.mark.parametrize(
    ("ma", "mom", "br", "label"),
    [
        (1, 1, 1, "UP"),
        (1, 1, 0, "NEUTRAL"),
        (1, 0, 0, "DOWN"),
        (0, 0, 0, "DOWN"),
        (0, 1, 1, "NEUTRAL"),
        (1, 1, np.nan, "UP"),
        (1, 0, np.nan, "NEUTRAL"),
        (0, 0, np.nan, "DOWN"),
        (np.nan, 1, 1, None),  # the benchmark MA vote is required
    ],
)
def test_classify_trend_all_vote_combinations(
    ma: float, mom: float, br: float, label: str | None
) -> None:
    idx = _days(1)
    votes = pd.DataFrame({"ma": [ma], "momentum": [mom], "breadth": [br]}, index=idx, dtype=float)
    assert classify_trend(votes, TREND).iloc[0] == label


def test_rising_and_falling_markets() -> None:
    n = 400
    idx = _days(n)
    up = pd.Series(100 * np.exp(np.linspace(0, 0.5, n)), index=idx)
    down = pd.Series(100 * np.exp(np.linspace(0, -0.5, n)), index=idx)
    for close, label in ((up, "UP"), (down, "DOWN")):
        ratio = close / close.rolling(200).mean()
        mom = close.shift(21) / close.shift(252) - 1
        votes = trend_votes(ratio, mom, pd.Series(np.nan, index=idx), TREND)
        assert (classify_trend(votes, TREND).iloc[260:] == label).all()
