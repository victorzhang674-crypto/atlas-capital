"""Stage 15 Tasks 7-13: every technical feature against the reference CSV (rtol 1e-6, atol 1e-9)
and against small hand-computed cases."""

import itertools
import math
import statistics
from datetime import date

import feature_reference as ref
import numpy as np
import pandas as pd
import pytest

from atlas.quant.features import REGISTRY, PricePanel, compute_panel

GROUPS = {
    "returns": (
        "ret_1d", "ret_5d", "ret_21d", "ret_63d", "ret_126d", "ret_252d", "momentum_12_1",
        "reversal_5d",
    ),
    "volatility": ("vol_21d", "vol_63d", "downside_vol_63d", "atr_14", "atr_pct_14"),
    "oscillators": ("rsi_14", "macd", "macd_signal", "macd_hist", "bb_pctb_20"),
    "averages": (
        "sma_20", "sma_50", "sma_200", "ema_20", "ema_50", "ema_200", "price_to_sma_20",
        "price_to_sma_50", "price_to_sma_200", "price_to_ema_20", "price_to_ema_50",
        "price_to_ema_200",
    ),
    "range": ("dist_52w_high", "dist_52w_low", "max_dd_252d"),
    "liquidity": ("volume_z_21d", "amihud_21d", "gap_1d", "gap_mean_21d", "gap_std_63d"),
    "market": ("beta_252d", "idio_vol_252d"),
}  # fmt: skip
CASES = [(group, name) for group, names in GROUPS.items() for name in names]
RTOL, ATOL = 1e-6, 1e-9


@pytest.fixture(scope="module")
def reference_values() -> dict[str, pd.DataFrame]:
    """The production features on `ohlcv.csv`, last 60 sessions, AAA and BBB."""
    bars = pd.DataFrame(ref.read_ohlcv())
    days = sorted(set(bars["date"]))
    panel = PricePanel.from_bars(bars, days, list(ref.TARGETS), benchmark=ref.BENCHMARK)
    names = [name for _, name in CASES if name in REGISTRY]
    return {k: v.iloc[-ref.EXPECTED_SESSIONS :] for k, v in compute_panel(panel, names).items()}


@pytest.fixture(scope="module")
def expected() -> dict[str, pd.DataFrame]:
    rows = ref.read_expected()
    out: dict[str, pd.DataFrame] = {}
    for i, name in enumerate(ref.FEATURE_NAMES):
        long = pd.DataFrame([(d, s, v[i]) for d, s, v in rows], columns=["date", "symbol", "v"])
        wide = long.pivot(index="date", columns="symbol", values="v")
        wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index), name="date")
        out[name] = wide[list(ref.TARGETS)]
    return out


def test_groups_cover_the_reference() -> None:
    assert sorted(name for _, name in CASES) == sorted(ref.FEATURE_NAMES)


@pytest.mark.parametrize(("group", "name"), CASES, ids=[f"{g}-{n}" for g, n in CASES])
def test_matches_reference(
    group: str,
    name: str,
    reference_values: dict[str, pd.DataFrame],
    expected: dict[str, pd.DataFrame],
) -> None:
    assert name in REGISTRY, f"{name} is not registered"
    got = reference_values[name]
    want = expected[name]
    assert list(got.index) == list(want.index)
    a, b = got.to_numpy(), want.to_numpy()
    assert (np.isnan(a) == np.isnan(b)).all(), f"{name}: NaN pattern differs"
    assert np.isfinite(b).sum() >= 60, f"{name}: too few reference values"
    np.testing.assert_allclose(a, b, rtol=RTOL, atol=ATOL, equal_nan=True, err_msg=name)


# --- hand-computed cases -----------------------------------------------------------------------


def _panel(
    close: list[float],
    *,
    high: list[float] | None = None,
    low: list[float] | None = None,
    open_: list[float] | None = None,
    dollar_volume: list[float] | None = None,
    bench: list[float] | None = None,
) -> PricePanel:
    idx = pd.DatetimeIndex(pd.bdate_range(date(2020, 1, 1), periods=len(close)), name="date")

    def col(values: list[float] | None) -> pd.DataFrame:
        return pd.DataFrame({"X": values if values is not None else close}, index=idx, dtype=float)

    b = pd.Series(bench, index=idx, dtype=float) if bench is not None else None
    dv = dollar_volume if dollar_volume is not None else [1e8] * len(close)
    return PricePanel(col(open_), col(high), col(low), col(close), col(dv), benchmark_close=b)


def _last(panel: PricePanel, name: str) -> float:
    return float(compute_panel(panel, [name])[name]["X"].iloc[-1])


def _series(panel: PricePanel, name: str) -> list[float]:
    return compute_panel(panel, [name])[name]["X"].tolist()


def test_returns_hand_values() -> None:
    p = _panel([100.0, 110.0, 99.0])
    assert _series(p, "ret_1d") == pytest.approx([math.nan, 0.1, -0.1], nan_ok=True)
    path = [100.0, 101.0, 99.0, 103.0, 104.0, 102.0, 106.0]
    assert _last(_panel(path), "ret_5d") == pytest.approx(106.0 / 101.0 - 1)
    assert _last(_panel(path), "reversal_5d") == pytest.approx(1 - 106.0 / 101.0)
    geometric = [1.001**t for t in range(300)]
    assert _last(_panel(geometric), "momentum_12_1") == pytest.approx(1.001**231 - 1)
    assert _last(_panel(geometric), "ret_252d") == pytest.approx(1.001**252 - 1)


def test_volatility_hand_values() -> None:
    closes = [100.0]
    for t in range(80):
        closes.append(closes[-1] * (1.01 if t % 2 else 0.99))
    rets = [b / a - 1 for a, b in itertools.pairwise(closes)]
    p = _panel(closes)
    assert _last(p, "vol_21d") == pytest.approx(statistics.stdev(rets[-21:]) * math.sqrt(252))
    down = math.sqrt(sum(min(r, 0) ** 2 for r in rets[-63:]) / 63) * math.sqrt(252)
    assert _last(p, "downside_vol_63d") == pytest.approx(down)
    flat = [50.0] * 40
    bars = _panel(flat, high=[51.0] * 40, low=[49.0] * 40)
    assert _last(bars, "atr_14") == pytest.approx(2.0)  # every true range is 2
    assert _last(bars, "atr_pct_14") == pytest.approx(0.04)


def test_oscillators_hand_values() -> None:
    rising = [100.0 + t for t in range(60)]
    assert _last(_panel(rising), "rsi_14") == 100.0
    flat = [100.0] * 60
    assert _last(_panel(flat), "rsi_14") == 50.0
    assert _last(_panel(flat), "macd") == pytest.approx(0.0, abs=1e-12)
    assert _last(_panel(flat), "macd_hist") == pytest.approx(0.0, abs=1e-12)
    assert math.isnan(_last(_panel(flat), "bb_pctb_20"))  # a flat band: no %B
    falling = [200.0 - t for t in range(60)]
    assert _last(_panel(falling), "rsi_14") == 0.0
    # %B of the last of 20 values 1..20: mid 10.5, population sd sqrt((20^2 - 1)/12)
    sd = math.sqrt((20**2 - 1) / 12)
    want = (20 - (10.5 - 2 * sd)) / (4 * sd)
    assert _last(_panel([float(v) for v in range(1, 21)]), "bb_pctb_20") == pytest.approx(want)


def test_averages_hand_values() -> None:
    linear = [float(t) for t in range(1, 251)]
    p = _panel(linear)
    assert _last(p, "sma_20") == pytest.approx(240.5)
    assert _last(p, "sma_200") == pytest.approx(150.5)
    assert _last(p, "price_to_sma_20") == pytest.approx(250 / 240.5)
    const = _panel([42.0] * 250)
    assert _last(const, "ema_200") == pytest.approx(42.0)
    assert _last(const, "price_to_ema_50") == pytest.approx(1.0)
    # EMA(20) one step after its seed: seed = mean of 1..20 = 10.5, then 10.5 + (2/21)(21 - 10.5)
    assert _series(_panel(linear[:21]), "ema_20")[-1] == pytest.approx(10.5 + 2 / 21 * 10.5)


def test_range_hand_values() -> None:
    path = [100.0, 120.0, 90.0, 130.0, 117.0] * 60
    p = _panel(path)
    assert _last(p, "max_dd_252d") == pytest.approx(90 / 130 - 1)
    assert _last(p, "dist_52w_high") == pytest.approx(117 / 130 - 1)
    assert _last(p, "dist_52w_low") == pytest.approx(117 / 90 - 1)
    edge = [100.0, 50.0] + [60.0] * 250  # the trough on the second bar of the window
    assert _last(_panel(edge), "max_dd_252d") == pytest.approx(-0.5)
    peak_late = [80.0] * 250 + [100.0, 70.0]  # the peak on the window's second-last bar
    assert _last(_panel(peak_late), "max_dd_252d") == pytest.approx(-0.3)
    up = [100.0 + t for t in range(260)]
    assert _last(_panel(up), "max_dd_252d") == 0.0
    assert _last(_panel(up), "dist_52w_high") == 0.0


def test_liquidity_hand_values() -> None:
    n = 30
    dv = [1e6 if t % 2 else 2e6 for t in range(n)]
    dv[-1] = 6e6
    p = _panel([100.0] * n, dollar_volume=dv)
    logs = [math.log(v) for v in dv]
    window = logs[-22:-1]
    z = (logs[-1] - statistics.mean(window)) / statistics.stdev(window)
    assert _last(p, "volume_z_21d") == pytest.approx(z)
    closes = [100.0]
    for t in range(30):
        closes.append(closes[-1] * (1.01 if t % 2 else 0.99))
    p2 = _panel(closes, dollar_volume=[1e8] * 31, open_=[closes[0], *closes[:-1]])
    assert _last(p2, "amihud_21d") == pytest.approx(0.01 / 1e8 * 1e6)
    assert _last(p2, "gap_1d") == pytest.approx(0.0, abs=1e-15)  # open = previous close
    opens = [c * 1.002 for c in [closes[0], *closes[:-1]]]  # every gap is +0.2%
    p3 = _panel(closes, open_=opens)
    assert _last(p3, "gap_mean_21d") == pytest.approx(0.002)
    assert _last(_panel([100.0] * 70, open_=[100.2] * 70), "gap_std_63d") == pytest.approx(
        0.0, abs=1e-12
    )


# --- Task 13: beta and idiosyncratic vol on synthetic data with a known beta ---------------------


def _exact_world(beta: float, idio_daily: float, n: int = 253, seed: int = 7) -> PricePanel:
    """Returns `r = 0.0002 + beta * m + e` with `e` exactly orthogonal to a constant and to `m`
    and with sample std (ddof 2 after the fit, as OLS reports) `idio_daily`."""
    rng = np.random.default_rng(seed)
    m = rng.normal(0.0004, 0.01, n - 1)
    basis = np.column_stack([np.ones(n - 1), m])
    e = rng.normal(0.0, 1.0, n - 1)
    e -= basis @ np.linalg.lstsq(basis, e, rcond=None)[0]
    e *= idio_daily / math.sqrt((e @ e) / (n - 3))
    r = 0.0002 + beta * m + e
    bench = list(100.0 * np.concatenate([[1.0], np.cumprod(1 + m)]))
    close = list(50.0 * np.concatenate([[1.0], np.cumprod(1 + r)]))
    return _panel(close, bench=bench)


def test_market_known_beta_exact() -> None:
    p = _exact_world(1.3, 0.012)
    assert _last(p, "beta_252d") == pytest.approx(1.3, abs=1e-9)
    assert _last(p, "idio_vol_252d") == pytest.approx(0.012 * math.sqrt(252), abs=1e-9)


def test_market_known_beta_noisy() -> None:
    rng = np.random.default_rng(42)
    n = 600
    m = rng.normal(0.0003, 0.01, n - 1)
    r = 1.3 * m + rng.normal(0.0, 0.003, n - 1)
    p = _panel(
        list(50.0 * np.concatenate([[1.0], np.cumprod(1 + r)])),
        bench=list(100.0 * np.concatenate([[1.0], np.cumprod(1 + m)])),
    )
    betas = pd.Series(_series(p, "beta_252d")).dropna()
    assert len(betas) == n - 252
    assert (betas - 1.3).abs().max() < 0.05
    idio = pd.Series(_series(p, "idio_vol_252d")).dropna()
    assert ((idio / (0.003 * math.sqrt(252))) - 1).abs().max() < 0.15


def test_market_without_benchmark_is_nan() -> None:
    p = _panel([100.0 + t for t in range(300)])  # no benchmark_close
    assert math.isnan(_last(p, "beta_252d"))
    assert math.isnan(_last(p, "idio_vol_252d"))
    gap = _exact_world(1.3, 0.012)
    assert gap.benchmark_close is not None
    holes = gap.benchmark_close.copy()
    holes.iloc[-10] = math.nan  # one missing benchmark bar inside the window
    p2 = PricePanel(gap.open, gap.high, gap.low, gap.close, gap.dollar_volume, holes)
    assert math.isnan(_last(p2, "beta_252d"))


def test_feature_specs_match_the_plan() -> None:
    lookbacks = {
        "ret_1d": 2, "ret_5d": 6, "ret_21d": 22, "ret_63d": 64, "ret_126d": 127, "ret_252d": 253,
        "momentum_12_1": 253, "reversal_5d": 6, "vol_21d": 22, "vol_63d": 64,
        "downside_vol_63d": 64, "atr_14": 15, "atr_pct_14": 15, "rsi_14": 15, "macd": 26,
        "macd_signal": 34, "macd_hist": 34, "bb_pctb_20": 20, "sma_20": 20, "sma_50": 50,
        "sma_200": 200, "ema_20": 20, "ema_50": 50, "ema_200": 200, "price_to_sma_20": 20,
        "price_to_sma_50": 50, "price_to_sma_200": 200, "price_to_ema_20": 20,
        "price_to_ema_50": 50, "price_to_ema_200": 200, "dist_52w_high": 252,
        "dist_52w_low": 252, "max_dd_252d": 252, "beta_252d": 253, "idio_vol_252d": 253,
        "volume_z_21d": 22, "amihud_21d": 22, "gap_1d": 2, "gap_mean_21d": 22, "gap_std_63d": 64,
    }  # fmt: skip
    assert {s.name: s.lookback for s in REGISTRY} == lookbacks
    usd = {
        "atr_14",
        "macd",
        "macd_signal",
        "macd_hist",
        *(f"{k}_{n}" for k in ("sma", "ema") for n in (20, 50, 200)),
    }
    assert {s.name for s in REGISTRY if s.unit == "usd"} == usd
    assert {s.name for s in REGISTRY if s.benchmark} == {"beta_252d", "idio_vol_252d"}
    assert all(s.kind == "ts" for s in REGISTRY)
    assert all(s.description for s in REGISTRY)
