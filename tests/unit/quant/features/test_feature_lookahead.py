"""Stage 15 Task 14: no look-ahead (shift and truncation tests for every feature, future lake
rows through `DataView`), lookbacks, `ts` invariance to the cross-section."""

from datetime import date

import feature_reference as ref
import numpy as np
import pandas as pd
import pytest

from atlas.data.view import DataView
from atlas.quant.features import REGISTRY, PricePanel, compute_features, compute_panel
from tiny_universe import TinyUniverse, add_future_rows

NAMES = REGISTRY.names()
CUTS = (260, 300, 320)  # session indices of the 330-session reference series


@pytest.fixture(scope="module")
def panel() -> PricePanel:
    bars = pd.DataFrame(ref.read_ohlcv())
    days = sorted(set(bars["date"]))
    return PricePanel.from_bars(bars, days, list(ref.TARGETS), benchmark=ref.BENCHMARK)


@pytest.fixture(scope="module")
def baseline(panel: PricePanel) -> dict[str, pd.DataFrame]:
    return compute_panel(panel)


def _perturbed(panel: PricePanel, cut: int, seed: int) -> PricePanel:
    """`panel` with every value after row `cut` replaced by random positive numbers."""
    rng = np.random.default_rng(seed)

    def scramble(frame: pd.DataFrame, scale: float = 1.0) -> pd.DataFrame:
        out = frame.copy()
        out.iloc[cut + 1 :] = scale * rng.uniform(1.0, 500.0, out.iloc[cut + 1 :].shape)
        return out

    bench = panel.benchmark_close
    assert bench is not None
    new_bench = bench.copy()
    new_bench.iloc[cut + 1 :] = rng.uniform(1.0, 500.0, len(bench) - cut - 1)
    return PricePanel(
        scramble(panel.open),
        scramble(panel.high),
        scramble(panel.low),
        scramble(panel.close),
        scramble(panel.dollar_volume, 1e6),
        new_bench,
    )


def _truncated(panel: PricePanel, cut: int) -> PricePanel:
    bench = panel.benchmark_close
    assert bench is not None
    rows = slice(None, cut + 1)
    return PricePanel(
        panel.open.iloc[rows],
        panel.high.iloc[rows],
        panel.low.iloc[rows],
        panel.close.iloc[rows],
        panel.dollar_volume.iloc[rows],
        bench.iloc[rows],
    )


@pytest.mark.parametrize("cut", CUTS)
@pytest.mark.parametrize("name", NAMES)
def test_shift_future_data_changes_nothing(
    panel: PricePanel, baseline: dict[str, pd.DataFrame], name: str, cut: int
) -> None:
    """Features at t use data <= t only: scrambling every later bar changes no value <= t."""
    changed = compute_panel(_perturbed(panel, cut, seed=cut), [name])[name]
    before = baseline[name].iloc[: cut + 1].to_numpy()
    after = changed.iloc[: cut + 1].to_numpy()
    np.testing.assert_array_equal(after, before, err_msg=name)
    if cut == CUTS[0]:  # not vacuous: the scrambled bars do reach later values
        later = baseline[name].iloc[cut + 1 :].to_numpy()
        moved = changed.iloc[cut + 1 :].to_numpy()
        assert not np.array_equal(later, moved, equal_nan=True), f"{name}: no effect"


@pytest.mark.parametrize("name", NAMES)
def test_truncating_the_future_changes_nothing(
    panel: PricePanel, baseline: dict[str, pd.DataFrame], name: str
) -> None:
    for cut in CUTS:
        short = compute_panel(_truncated(panel, cut), [name])[name]
        np.testing.assert_array_equal(
            short.to_numpy(), baseline[name].iloc[: cut + 1].to_numpy(), err_msg=f"{name}@{cut}"
        )


@pytest.fixture(scope="module")
def clean_panel() -> PricePanel:
    """300 sessions of two random symbols and a benchmark, no missing bar."""
    rng = np.random.default_rng(3)
    n = 300
    idx = pd.DatetimeIndex(pd.bdate_range("2020-01-01", periods=n), name="date")
    rets = rng.normal(0.0003, 0.012, (n, 3))
    closes = 100 * np.cumprod(1 + rets, axis=0)
    close = pd.DataFrame(closes[:, :2], index=idx, columns=["X", "Y"])
    open_ = close.shift(1).fillna(100.0) * (1 + rng.normal(0, 0.003, (n, 2)))
    high = np.maximum(open_, close) * 1.004
    low = np.minimum(open_, close) * 0.996
    dv = pd.DataFrame(rng.lognormal(18, 0.4, (n, 2)), index=idx, columns=["X", "Y"])
    return PricePanel(open_, high, low, close, dv, pd.Series(closes[:, 2], index=idx))


@pytest.mark.parametrize("name", NAMES)
def test_first_value_at_lookback(clean_panel: PricePanel, name: str) -> None:
    """Exactly `lookback` sessions of bars give the first value (row `lookback - 1`)."""
    values = compute_panel(clean_panel, [name])[name]
    lookback = REGISTRY.get(name).lookback
    for column in values.columns:
        valid = values[column].notna().to_numpy()
        assert valid.argmax() == lookback - 1, (name, column)
        assert valid[lookback - 1 :].all(), (name, column)


@pytest.mark.parametrize("name", NAMES)
def test_ts_features_ignore_other_symbols(clean_panel: PricePanel, name: str) -> None:
    assert REGISTRY.get(name).kind == "ts"
    both = compute_panel(clean_panel, [name])[name]
    p = clean_panel
    alone = PricePanel(
        p.open[["X"]], p.high[["X"]], p.low[["X"]], p.close[["X"]], p.dollar_volume[["X"]],
        p.benchmark_close,
    )  # fmt: skip
    np.testing.assert_array_equal(compute_panel(alone, [name])[name]["X"], both["X"])


def test_forty_features_registered() -> None:
    assert len(REGISTRY) == 40
    assert set(NAMES) == set(ref.FEATURE_NAMES)


# --- through DataView: rows known only after the as-of never change the answer ------------------


def test_view_future_rows_never_leak(tiny_universe: TinyUniverse) -> None:
    symbols = list(tiny_universe.symbols)
    clean = compute_features(tiny_universe.view(), symbols, history=40, layout="long")
    add_future_rows(tiny_universe)
    dirty = compute_features(tiny_universe.view(), symbols, history=40, layout="long")
    pd.testing.assert_frame_equal(dirty, clean)
    last = clean.index.get_level_values("date").max()
    assert last == pd.Timestamp(date(2024, 2, 29))
    later = DataView(tiny_universe.lake, date(2024, 3, 18))
    moved = compute_features(later, symbols, history=40, layout="long")
    assert moved.index.get_level_values("date").max() == pd.Timestamp(date(2024, 3, 15))
    assert not moved.equals(clean)  # the injected rows do matter once known
