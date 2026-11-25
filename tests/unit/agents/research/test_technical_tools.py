"""Stage 30 Tasks 5-7: the technical analyst's tools. Expected numbers are computed here
from the world's raw bars (split-adjusted by hand), not by the code under test."""

from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest
from technical_world import (
    AS_OF,
    CONFIG,
    LAST,
    MEMBERS,
    SPLIT_DAY,
    SPLIT_RATIO,
    TechnicalWorld,
)

from atlas.agents.context import AgentContext
from atlas.agents.tools import technical_tools as tt
from atlas.core.errors import ConfigError, DataError
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.core.types.research import Evidence
from atlas.data.view import DataView
from atlas.quant.features import REGISTRY, build_snapshots, compute_features
from atlas.quant.regime import RegimeState, regime_at

CUTOFF_ITEM = datetime(2024, 3, 1, 13, 0, tzinfo=UTC) - timedelta(microseconds=1)
"""The decision cutoff (08:00 New York = 13:00 UTC on 2024-03-01) minus 1 µs."""


def adjusted(world: TechnicalWorld, symbol: str, n: int | None = None) -> pd.DataFrame:
    """`symbol`'s bars to `LAST`, OHLC adjusted for the SPL split by hand; the last `n`."""
    bars = world.bars[world.bars["symbol"] == symbol].sort_values("date").copy()
    if symbol == "SPL":
        before = bars["date"] < SPLIT_DAY
        for col in ("open", "high", "low", "close"):
            bars.loc[before, col] = bars.loc[before, col] / SPLIT_RATIO
    bars = bars.reset_index(drop=True)
    return bars if n is None else bars.tail(n).reset_index(drop=True)


def by_id(items: list[Evidence]) -> dict[str, Evidence]:
    return {e.id: e for e in items}


def regime(day: date = AS_OF, **kw: object) -> RegimeState:
    base: dict[str, object] = {
        "as_of": day,
        "data_date": date(2024, 2, 29),
        "vol_regime": "HIGH",
        "trend_regime": "UP",
        "hmm_probs": (0.1, 0.2, 0.7),
        "hmm_labels": ("calm", "normal", "turbulent"),
        "macro_quadrant": "Reflation",
        "stress_flag": True,
        "risk_scalar": 0.36,
        "credit_source": "proxy",
        "realised_vol": 0.231,
        "vix": 27.4,
        "vol_score": 0.88,
        "price_to_sma_200": 1.0612,
        "momentum_12_1": 0.154,
        "breadth": 0.62,
        "credit_z": 2.31,
        "nfci": -0.42,
        "growth_score": 0.25,
        "inflation_gap": -0.8,
    }
    return RegimeState.model_validate({**base, **kw})


# --- get_price_summary ------------------------------------------------------------------------


def test_the_summary_has_five_items_with_ids_sources_and_times(tworld: TechnicalWorld) -> None:
    items = tt.price_summary(tworld.view(), "UPP")
    assert [e.id for e in items] == [
        f"pxs:UPP:2024-02-29:{part}" for part in ("ohlc", "range", "ma", "levels", "stops")
    ]
    last_bar = tworld.bars.loc[tworld.bars["symbol"] == "UPP", "available_at"].max()
    for item in items:
        assert item.source == "prices"
        assert item.as_of == last_bar.to_pydatetime()
        assert item.as_of < CUTOFF_ITEM


def test_the_ohlc_table_is_split_adjusted(tworld: TechnicalWorld) -> None:
    n = CONFIG.agents.technical.ohlc_sessions
    view = tworld.view(day=date(2023, 11, 21))  # five sessions after the split
    text = tt.price_summary(view, "SPL")[0].text
    bars = adjusted(tworld, "SPL")
    bars = bars[bars["date"] <= date(2023, 11, 20)].tail(n)
    assert text.count(" O ") == n and text.endswith("M.")
    for row in bars.itertuples():
        volume = row.volume * (SPLIT_RATIO if row.date < SPLIT_DAY else 1.0)
        assert (
            f"{row.date} O {row.open:.2f} H {row.high:.2f} L {row.low:.2f} C {row.close:.2f} "
            f"V {volume / 1e6:.2f}M" in text
        )
    assert "adjusted for splits and dividends known today" in text


def test_the_52_week_range(tworld: TechnicalWorld) -> None:
    bars = adjusted(tworld, "SPL", 252)
    hi, lo = bars["high"].idxmax(), bars["low"].idxmin()
    high, low, close = bars["high"][hi], bars["low"][lo], bars["close"].iloc[-1]
    text = by_id(tt.price_summary(tworld.view(), "SPL"))["pxs:SPL:2024-02-29:range"].text
    assert f"252 sessions {bars['date'][0]}..2024-02-29" in text
    assert f"high {high:.2f} on {bars['date'][hi]}" in text
    assert f"low {low:.2f} on {bars['date'][lo]}" in text
    assert f"{(close / high - 1) * 100:+.2f}% from the high" in text
    assert f"{(close / low - 1) * 100:+.2f}% from the low" in text
    assert f"at {(close - low) / (high - low) * 100:.1f}% of the range" in text
    assert "only" not in text


def test_a_short_history_says_so(tworld: TechnicalWorld) -> None:
    items = by_id(tt.price_summary(tworld.view(), "NEW"))
    first = adjusted(tworld, "NEW")["date"].iloc[0]
    assert (
        f"(only 80 sessions of history, first bar {first})"
        in items["pxs:NEW:2024-02-29:range"].text
    )
    assert "SMA200 n/a" in items["pxs:NEW:2024-02-29:ma"].text
    assert "252-session" not in items["pxs:NEW:2024-02-29:levels"].text
    assert "52-week (80-session) low" in items["pxs:NEW:2024-02-29:levels"].text


def test_moving_averages_are_the_stage_15_features(tworld: TechnicalWorld) -> None:
    view = tworld.view()
    feats = compute_features(view, ["UPP"], ["sma_20", "sma_50", "sma_200"])
    close = adjusted(tworld, "UPP")["close"].iloc[-1]
    text = by_id(tt.price_summary(view, "UPP"))["pxs:UPP:2024-02-29:ma"].text
    for name, label in (("sma_20", "SMA20"), ("sma_50", "SMA50"), ("sma_200", "SMA200")):
        value = float(feats[name]["UPP"].iloc[-1])
        assert f"{label} {value:.2f} (close {(close / value - 1) * 100:+.2f}% vs it)" in text
    s50, s200 = (float(feats[n]["UPP"].iloc[-1]) for n in ("sma_50", "sma_200"))
    assert s50 > s200
    assert f"SMA50 is {(s50 / s200 - 1) * 100:.2f}% above SMA200" in text
    dwn = by_id(tt.price_summary(view, "DWN"))["pxs:DWN:2024-02-29:ma"].text
    d50, d200 = (
        float(compute_features(view, ["DWN"], [n])[n]["DWN"].iloc[-1])
        for n in ("sma_50", "sma_200")
    )
    assert f"SMA50 is {(1 - d50 / d200) * 100:.2f}% below SMA200" in dwn


def test_levels_split_into_support_below_and_resistance_above(tworld: TechnicalWorld) -> None:
    bars = adjusted(tworld, "DWN")
    close = bars["close"].iloc[-1]
    text = by_id(tt.price_summary(tworld.view(), "DWN"))["pxs:DWN:2024-02-29:levels"].text
    support, resistance = text.split("Resistance above")
    for window in (20, 63):
        tail = bars.tail(window).reset_index(drop=True)
        lo, hi = tail["low"].idxmin(), tail["high"].idxmax()
        assert (
            f"{window}-session low {tail['low'][lo]:.2f} on {tail['date'][lo]} "
            f"({(tail['low'][lo] / close - 1) * 100:+.2f}%)" in support
        )
        assert (
            f"{window}-session high {tail['high'][hi]:.2f} on {tail['date'][hi]} "
            f"({(tail['high'][hi] / close - 1) * 100:+.2f}%)" in resistance
        )
    # DWN falls steadily: every moving average is above the close
    for label in ("SMA20", "SMA50", "SMA200"):
        assert label in resistance and label not in support


def test_stop_references(tworld: TechnicalWorld) -> None:
    view = tworld.view()
    atr = float(compute_features(view, ["UPP"], ["atr_14"])["atr_14"]["UPP"].iloc[-1])
    bars = adjusted(tworld, "UPP")
    close = bars["close"].iloc[-1]
    text = by_id(tt.price_summary(view, "UPP"))["pxs:UPP:2024-02-29:stops"].text
    assert f"ATR(14) {atr:.2f} USD = {atr / close * 100:.2f}% of the last close {close:.2f}" in text
    for k in (2.0, 3.0):
        assert f"{k:g} x ATR = {k * atr / close * 100:.2f}%" in text
    for window in (20, 63):
        tail = bars.tail(window)
        long_stop = 1 - tail["low"].min() / close
        short_stop = tail["high"].max() / close - 1
        assert f"{window}-session low {long_stop * 100:.2f}% (LONG)" in text
        assert f"{window}-session high {short_stop * 100:.2f}% (SHORT)" in text


def test_a_stale_last_bar_is_named(tworld: TechnicalWorld, tmp_path: object) -> None:
    lake = Lake(tmp_path / "stale", registry=DEFAULT_REGISTRY.copy())  # type: ignore[operator]
    bars = tworld.bars[
        ~((tworld.bars["symbol"] == "M01") & (tworld.bars["date"] > date(2024, 2, 26)))
    ]
    lake.write_table("prices_daily", bars, mode="upsert")
    items = tt.price_summary(DataView(lake, AS_OF, CONFIG), "M01")
    assert items[0].id == "pxs:M01:2024-02-26:ohlc"
    assert "last bar 2024-02-26 is 3 sessions before the latest session 2024-02-29" in items[0].text
    # the 52-week window ends at the last bar: 252 sessions, not 249 of a window ending later
    bars = adjusted(tworld, "M01")
    bars = bars[bars["date"] <= date(2024, 2, 26)].tail(252)
    range_text = items[1].text
    assert f"252 sessions {bars['date'].iloc[0]}..2024-02-26)" in range_text
    assert "only" not in range_text and "without a bar" not in range_text
    assert f"high {bars['high'].max():.2f}" in range_text


def test_missing_bars_inside_the_window_are_counted(
    tworld: TechnicalWorld, tmp_path: object
) -> None:
    lake = Lake(tmp_path / "gaps", registry=DEFAULT_REGISTRY.copy())  # type: ignore[operator]
    gone = {date(2023, 6, 1), date(2023, 6, 2), date(2023, 6, 5)}
    bars = tworld.bars[~((tworld.bars["symbol"] == "M02") & tworld.bars["date"].isin(gone))]
    lake.write_table("prices_daily", bars, mode="upsert")
    text = tt.price_summary(DataView(lake, AS_OF, CONFIG), "M02")[1].text
    assert "(249 sessions 2023-03-01..2024-02-29) (3 sessions in the window without a bar)" in text
    levels = tt.price_summary(DataView(lake, AS_OF, CONFIG), "M02")[3].text
    assert "52-week low" in levels and "-session) low" not in levels


def test_a_symbol_without_bars_in_the_window_fails(tworld: TechnicalWorld) -> None:
    with pytest.raises(DataError, match="no bars known for ZZZ in the 252 sessions to 2024-03-01"):
        tt.price_summary(tworld.view(), "ZZZ")


def test_a_blocked_symbol_fails(tworld: TechnicalWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    view = tworld.view()
    monkeypatch.setattr(DataView, "blocked", property(lambda self: frozenset({"UPP"})))
    with pytest.raises(DataError, match="blocked"):
        tt.price_summary(view, "UPP")
    with pytest.raises(DataError, match="blocked"):
        tt.technical_snapshot(view, "UPP")


# --- get_technical_snapshot -------------------------------------------------------------------


def test_every_registered_feature_is_in_exactly_one_group() -> None:
    grouped = [name for names in tt.FEATURE_GROUPS.values() for name in names]
    assert len(grouped) == len(set(grouped))
    assert set(grouped) == set(REGISTRY.names())
    assert tuple(tt.FEATURE_GROUPS) == (
        "trend",
        "momentum",
        "volatility",
        "levels",
        "participation",
    )


def test_snapshot_items_show_values_and_percentiles(tworld: TechnicalWorld) -> None:
    view = tworld.view()
    snap = build_snapshots(view, ["UPP"])["UPP"]
    items = by_id(tt.technical_snapshot(view, "UPP"))
    assert list(items) == [f"tsnap:UPP:2024-02-29:{g}" for g in tt.FEATURE_GROUPS]
    for item in items.values():
        assert item.source == "technical_features" and item.as_of == CUTOFF_ITEM
    momentum = items["tsnap:UPP:2024-02-29:momentum"].text
    r = snap.reading("ret_252d")
    assert (
        f"ret_252d {r.value * 100:+.2f}% (own {r.pct_own * 100:.0f}, "  # type: ignore[operator]
        f"universe {r.pct_universe * 100:.0f})" in momentum  # type: ignore[operator]
    )
    rsi = snap.reading("rsi_14")
    assert f"rsi_14 {rsi.value:.1f}" in momentum
    trend = items["tsnap:UPP:2024-02-29:trend"].text
    sma = snap.reading("sma_200")
    assert f"sma_200 USD {sma.value:.2f} (own {sma.pct_own * 100:.0f}, universe n/a)" in trend  # type: ignore[operator]
    vol = items["tsnap:UPP:2024-02-29:volatility"].text
    v = snap.reading("vol_21d")
    assert f"vol_21d {v.value * 100:.2f}% annualised" in vol  # type: ignore[operator]
    assert "against 24 names" in momentum and "own last 252 sessions" in momentum


def test_undefined_values_read_n_a(tworld: TechnicalWorld) -> None:
    items = by_id(tt.technical_snapshot(tworld.view(), "NEW"))
    assert "ret_252d n/a (own n/a, universe n/a)" in items["tsnap:NEW:2024-02-29:momentum"].text


def test_every_unit_is_formatted() -> None:
    assert tt.fmt_feature(0.0123, "fraction") == "+1.23%"
    assert tt.fmt_feature(0.2, "annual_fraction") == "20.00% annualised"
    assert tt.fmt_feature(182.456, "usd") == "USD 182.46"
    assert tt.fmt_feature(1.04567, "ratio") == "1.046"
    assert tt.fmt_feature(61.27, "index_0_100") == "61.3"
    assert tt.fmt_feature(-1.234, "zscore") == "-1.23 sd"
    assert tt.fmt_feature(0.000123456, "return_per_usd_mm") == "0.000123 per USD 1M"
    assert tt.fmt_feature(None, "ratio") == "n/a"


# --- the market regime ------------------------------------------------------------------------


def test_the_regime_item_from_a_given_state(tworld: TechnicalWorld) -> None:
    item = tt.regime_item(tworld.view(), regime())
    assert item.id == "regime:2024-03-01:market"
    assert item.source == "regime" and item.as_of == CUTOFF_ITEM
    text = item.text
    for part in (
        "Market regime for decisions on 2024-03-01 (data through 2024-02-29; benchmark SPY)",
        "volatility regime HIGH (vol score 88, SPY 21-session realised vol 23.10% annualised, "
        "VIX 27.4)",
        "trend regime UP (SPY / its 200-session average 1.061, SPY 12-1 momentum +15.40%, "
        "breadth 62% of members above their 200-session average)",
        "HMM state probabilities calm 0.10, normal 0.20, turbulent 0.70",
        "macro quadrant Reflation (growth score +0.25, inflation gap -0.80 points)",
        "stress flag ON (credit z-score +2.31 from proxy, NFCI -0.42)",
        "risk scalar 0.36 (fraction of normal fund risk)",
    ):
        assert part in text, part
    assert "Missing" not in text


def test_missing_regime_parts_read_n_a(tworld: TechnicalWorld) -> None:
    state = regime(
        inflation_gap=None,
        hmm_probs=None, hmm_labels=(), macro_quadrant=None, vix=None, credit_z=None,
        credit_source=None, nfci=None, stress_flag=False, missing=("vix", "hmm"),
    )  # fmt: skip
    text = tt.regime_item(tworld.view(), state).text
    assert "VIX n/a" in text and "HMM state probabilities n/a" in text
    assert "inflation gap -0.80 points" not in text and "inflation gap n/a)" in text
    assert "macro quadrant n/a" in text and "stress flag off (credit z-score n/a, NFCI n/a)" in text
    assert "Missing components: vix, hmm." in text


def test_without_a_given_state_the_regime_is_computed(tworld: TechnicalWorld) -> None:
    view = tworld.view()
    assert tt.regime_item(view, None) == tt.regime_item(view, regime_at(view))


def test_a_regime_of_another_day_is_refused(tworld: TechnicalWorld) -> None:
    with pytest.raises(ConfigError, match="regime for 2024-03-04"):
        tt.regime_item(tworld.view(), regime(date(2024, 3, 4)))


# --- prefetch and the tools -------------------------------------------------------------------


def test_prefetch_is_the_summary_the_snapshot_and_the_regime(tworld: TechnicalWorld) -> None:
    view = tworld.view()
    items = tt.prefetch(view, "UPP", regime())
    assert items == [
        *tt.price_summary(view, "UPP"),
        *tt.technical_snapshot(view, "UPP"),
        tt.regime_item(view, regime()),
    ]


def test_a_regime_that_cannot_be_computed_is_a_gap(
    tmp_path: object, tworld: TechnicalWorld
) -> None:
    lake = Lake(tmp_path / "nospy", registry=DEFAULT_REGISTRY.copy())  # type: ignore[operator]
    lake.write_table("prices_daily", tworld.bars[tworld.bars["symbol"] != "SPY"], mode="upsert")
    view = DataView(lake, AS_OF, CONFIG)
    items = tt.prefetch(view, "UPP")
    gap = items[-1]
    assert gap.id == "gap:UPP:2024-03-01:market_regime"
    assert gap.text.startswith(
        "market_regime could not be computed for UPP: no bars for the benchmark SPY"
    )


def test_a_snapshot_failure_is_a_gap(
    tworld: TechnicalWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kw: object) -> object:
        raise DataError("feature store unavailable")

    monkeypatch.setattr(tt, "build_snapshots", broken)
    items = by_id(tt.prefetch(tworld.view(), "UPP", regime()))
    gap = items["gap:UPP:2024-03-01:get_technical_snapshot"]
    assert (
        gap.text
        == "get_technical_snapshot could not be computed for UPP: feature store unavailable"
    )
    assert "regime:2024-03-01:market" in items


def test_a_price_summary_failure_fails_prefetch(tworld: TechnicalWorld) -> None:
    with pytest.raises(DataError, match="no bars known for ZZZ"):
        tt.prefetch(tworld.view(), "ZZZ", regime())


def test_the_tools(tworld: TechnicalWorld) -> None:
    assert [t.name for t in tt.TECHNICAL_TOOLS] == ["get_technical_snapshot", "get_price_summary"]
    assert all(t.version == 1 and t.args is tt.TechnicalArgs for t in tt.TECHNICAL_TOOLS)
    snapshot_tool, summary_tool = tt.TECHNICAL_TOOLS
    view = tworld.view()
    ctx = AgentContext(view, inputs={"symbol": "UPP", "regime": regime()})
    args = tt.TechnicalArgs(symbol=" m02 ")
    assert args.symbol == "M02"
    assert list(summary_tool.fn(ctx, args)) == tt.price_summary(view, "M02")
    assert list(snapshot_tool.fn(ctx, args)) == [
        *tt.technical_snapshot(view, "M02"),
        tt.regime_item(view, regime()),
    ]
    bare = AgentContext(view, inputs={"symbol": "UPP"})
    assert list(snapshot_tool.fn(bare, args))[-1] == tt.regime_item(view, regime_at(view))


def test_a_regime_input_must_be_a_regime_state(tworld: TechnicalWorld) -> None:
    with pytest.raises(ConfigError, match="RegimeState"):
        tt.given_regime(AgentContext(tworld.view(), inputs={"regime": {"vol": "HIGH"}}))
    assert tt.given_regime(AgentContext(tworld.view(), inputs={})) is None


def test_members_are_the_world(tworld: TechnicalWorld) -> None:
    assert set(tworld.view().universe("sp500")) == set(MEMBERS)
    assert date(2024, 2, 29) == LAST
