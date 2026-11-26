"""Stage 30 audit: edges the first test round left open (found by mutation testing). Short
histories, flat prices, a stale symbol's averages, the regime text branches, a symbol with no
bars in the snapshot, and the exact report-check messages."""

import math
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest
from technical_world import AS_OF, CONFIG, TechnicalWorld, write_world

from atlas.agents.context import AgentContext
from atlas.agents.research import TechnicalAnalyst
from atlas.agents.research.technical import SIGNALS
from atlas.agents.tools import technical_tools as tt
from atlas.agents.tools.registry import ToolRegistry
from atlas.agents.trace import TraceStore
from atlas.core.errors import ConfigError, DataError
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.core.types.research import Direction, TechnicalReport, TechnicalSignal, Timing
from atlas.data.view import DataView
from atlas.llm import FakeLLM, LLMGateway
from atlas.quant.features import compute_features
from atlas.quant.regime import RegimeState


def lake_with(tworld: TechnicalWorld, root: Path, bars: pd.DataFrame) -> DataView:
    lake = Lake(root, registry=DEFAULT_REGISTRY.copy())
    write_world(lake, bars)
    return DataView(lake, AS_OF, CONFIG)


def last_sessions(tworld: TechnicalWorld, symbol: str, n: int) -> pd.DataFrame:
    """The world's bars with `symbol` cut to its last `n` bars."""
    bars = tworld.bars
    mine = bars[bars["symbol"] == symbol].sort_values("date").tail(n)
    return pd.concat([bars[bars["symbol"] != symbol], mine], ignore_index=True)


# --- formatting and short histories -----------------------------------------------------------


@pytest.mark.parametrize("unit", ["fraction", "usd", "ratio", "zscore"])
def test_a_nan_feature_reads_n_a(unit: str) -> None:
    assert tt.fmt_feature(math.nan, unit) == "n/a"  # type: ignore[arg-type]
    assert tt.fmt_feature(math.inf, unit) == "n/a"  # type: ignore[arg-type]


def test_ten_bars_give_no_swing_levels_no_atr_and_no_averages(
    tworld: TechnicalWorld, tmp_path: Path
) -> None:
    view = lake_with(tworld, tmp_path / "ten", last_sessions(tworld, "M03", 10))
    items = {e.id.rsplit(":", 1)[1]: e.text for e in tt.price_summary(view, "M03")}
    close = float(tworld.bars[tworld.bars["symbol"] == "M03"].sort_values("date")["close"].iloc[-1])
    assert "(only 10 sessions of history, first bar" in items["range"]
    assert "SMA20 n/a; SMA50 n/a; SMA200 n/a. SMA50 vs SMA200 n/a." in items["ma"]
    assert (
        "(no 20-session level (only 10 sessions); no 63-session level (only 10 sessions))"
        in (items["levels"])
    )
    assert "52-week (10-session) low" in items["levels"]
    assert (
        f"ATR(14) n/a (last close {close:.2f}). Stop distances as a percentage of the last close "
        "(below it for a LONG view, above it for a SHORT view): ATR multiples n/a; "
        "no swing levels." in items["stops"]
    )


def test_exactly_twenty_bars_give_the_20_session_level(
    tworld: TechnicalWorld, tmp_path: Path
) -> None:
    view = lake_with(tworld, tmp_path / "twenty", last_sessions(tworld, "M03", 20))
    items = {e.id.rsplit(":", 1)[1]: e.text for e in tt.price_summary(view, "M03")}
    assert "20-session low" in items["levels"]
    assert "(no 63-session level (only 20 sessions))" in items["levels"]
    assert "to the 20-session low" in items["stops"] and "63-session" not in items["stops"]
    assert "ATR(14) n/a" not in items["stops"]


def test_one_missing_first_bar_is_a_short_history(tworld: TechnicalWorld, tmp_path: Path) -> None:
    view = lake_with(tworld, tmp_path / "first", last_sessions(tworld, "M04", 251))
    text = tt.price_summary(view, "M04")[1].text
    assert "(only 251 sessions of history, first bar" in text


def test_a_stale_young_symbols_window_ends_at_its_last_bar(
    tworld: TechnicalWorld, tmp_path: Path
) -> None:
    """251 bars that end 3 sessions before the latest: the 252-session window ending at the
    last bar starts one session before the first bar, so this is a short history."""
    bars = tworld.bars
    mine = bars[(bars["symbol"] == "M06") & (bars["date"] <= date(2024, 2, 26))]
    mine = mine.sort_values("date").tail(251)
    view = lake_with(
        tworld, tmp_path / "stale-young", pd.concat([bars[bars["symbol"] != "M06"], mine])
    )
    text = tt.price_summary(view, "M06")[1].text
    assert (
        f"(251 sessions {mine['date'].iloc[0]}..2024-02-26) (only 251 sessions of history" in text
    )


def test_flat_prices(tworld: TechnicalWorld) -> None:
    days = [date(2024, 2, d) for d in (26, 27, 28, 29)]
    bars = pd.DataFrame(
        {
            "date": days,
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 1e6,
        }
    )
    assert tt._range_text("FLT", bars, 4, days[0]).endswith(" from the low, range n/a.")
    mas: dict[str, float | None] = {"sma_20": 10.0, "sma_50": 10.0, "sma_200": 10.0}
    assert "SMA50 is 0.00% above SMA200" in tt._ma_text("FLT", 10.0, mas)
    levels = tt._levels_text("FLT", bars, mas, CONFIG.agents.technical, days[0])
    assert "Resistance above: none." in levels
    assert "Support below: 52-week low 10.00 on 2024-02-26 (+0.00%)" in levels


# --- a stale symbol and the snapshot ----------------------------------------------------------


def test_a_stale_symbols_averages_and_atr_are_read_at_its_last_bar(
    tworld: TechnicalWorld, tmp_path: Path
) -> None:
    bars = tworld.bars
    stale = bars[~((bars["symbol"] == "M05") & (bars["date"] > date(2024, 2, 26)))]
    view = lake_with(tworld, tmp_path / "stale", stale)
    feats = compute_features(view, ["M05"], ["sma_50", "atr_14"], history=4)
    sma50 = float(feats["sma_50"]["M05"].loc[pd.Timestamp("2024-02-26")])
    atr = float(feats["atr_14"]["M05"].loc[pd.Timestamp("2024-02-26")])
    items = {e.id.rsplit(":", 1)[1]: e.text for e in tt.price_summary(view, "M05")}
    assert f"SMA50 {sma50:.2f} (close" in items["ma"]
    assert f"ATR(14) {atr:.2f} USD" in items["stops"]


def test_a_snapshot_of_a_symbol_without_bars_fails(tworld: TechnicalWorld) -> None:
    with pytest.raises(DataError, match="no technical features for ZZZ on 2024-02-29"):
        tt.technical_snapshot(tworld.view(), "ZZZ")


# --- the regime text --------------------------------------------------------------------------


def _regime(**kw: object) -> RegimeState:
    base: dict[str, object] = {
        "as_of": AS_OF,
        "data_date": date(2024, 2, 29),
        "vol_regime": "LOW",
        "trend_regime": "NEUTRAL",
        "hmm_probs": None,
        "hmm_labels": (),
        "macro_quadrant": None,
        "stress_flag": False,
        "risk_scalar": 0.9,
    }
    return RegimeState.model_validate({**base, **kw})


def test_a_credit_source_without_a_z_score_is_not_named(tworld: TechnicalWorld) -> None:
    text = tt.regime_item(tworld.view(), _regime(credit_source="oas")).text
    assert "(credit z-score n/a, NFCI n/a)" in text


def test_a_regime_input_of_the_wrong_type_is_named(tworld: TechnicalWorld) -> None:
    ctx = AgentContext(tworld.view(), inputs={"regime": {"vol": "HIGH"}})
    with pytest.raises(ConfigError, match="must be a RegimeState, not dict"):
        tt.given_regime(ctx)


# --- the analyst: exact messages, registry, memory --------------------------------------------


def _report(**kw: object) -> TechnicalReport:
    signals = tuple(
        TechnicalSignal(
            name=n,
            direction=Direction.FLAT,
            strength=0.5,
            evidence_ids=("pxs:UPP:2024-02-29:ma",),
            timing=Timing.AVOID,
        )
        for n in SIGNALS
    )
    base: dict[str, object] = {
        "ticker": "UPP",
        "as_of": AS_OF,
        "analyst": "technical_analyst",
        "summary": "Trend: flat.",
        "signals": signals,
        "risks": (),
        "confidence": 0.5,
    }
    return TechnicalReport.model_validate({**base, **kw})


def _analyst() -> TechnicalAnalyst:
    return TechnicalAnalyst(LLMGateway(FakeLLM({}), CONFIG), config=CONFIG, traces=TraceStore())


def test_the_check_messages_are_exact(tworld: TechnicalWorld) -> None:
    ctx = AgentContext(tworld.view(), inputs={"symbol": "UPP"})
    bad = _report(
        signals=(),
        summary="Trend: up 5%.",
        risks=("Support at 182.5 may break.",),
    )
    assert list(_analyst().check_output(ctx, bad)) == [
        "signals must be exactly trend, momentum, volatility, levels (each once); got none",
        "the summary needs these sections, in order, each starting a line with its label and a "
        'colon: "Momentum:", "Volatility regime:", "Levels:", "Timing and entry:", '
        '"Invalidation:"',
        "every sentence that states a number needs an [E:<id>] tag of its evidence; these have "
        'none: "Trend: up 5%."; "risks[0]: Support at 182.5 may break."',
    ]
    two = _report(signals=_report().signals[:2])
    assert "got trend, momentum" in _analyst().check_output(ctx, two)[0]


def test_the_symbol_message_is_exact(tworld: TechnicalWorld) -> None:
    with pytest.raises(ConfigError) as err:
        TechnicalAnalyst.symbol(AgentContext(tworld.view(), inputs={}))
    assert str(err.value) == "technical_analyst needs inputs['symbol'] (a ticker)"


def test_a_given_tool_registry_is_kept() -> None:
    from dataclasses import replace

    newer = replace(tt.GET_PRICE_SUMMARY, version=2)
    registry = ToolRegistry([tt.GET_TECHNICAL_SNAPSHOT, newer])
    agent = TechnicalAnalyst(
        LLMGateway(FakeLLM({}), CONFIG), config=CONFIG, traces=TraceStore(), tool_registry=registry
    )
    assert agent.prompt_version.endswith("|get_price_summary@v2,get_technical_snapshot@v1")


def test_the_memory_query_names_the_symbol(
    tworld: TechnicalWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[tuple[object, ...]] = []

    def recall(
        self: AgentContext,
        agent: str,
        query: str,
        k: object = None,
        symbol: object = None,
        **kw: object,
    ) -> list[object]:
        asked.append((agent, query, symbol))
        return []

    monkeypatch.setattr(AgentContext, "recall", recall)
    fake = FakeLLM({"technical_analyst": _report()})
    agent = TechnicalAnalyst(LLMGateway(fake, CONFIG), config=CONFIG, traces=TraceStore())
    agent.run(AgentContext(tworld.view(), inputs={"symbol": "UPP"}))
    assert asked == [
        (
            "technical_analyst",
            "UPP technicals: trend, momentum, volatility, levels, entry timing",
            "UPP",
        )
    ]


def test_memory_recall_is_for_the_runs_symbol(tworld: TechnicalWorld) -> None:
    from atlas.agents.memory import AgentMemory, HashEmbedder
    from atlas.core.config import AtlasConfig

    cfg = AtlasConfig.model_validate(
        {"agents": {"memory": {"embedder": "hash", "min_similarity": 0.0}}}
    )
    memory = AgentMemory(HashEmbedder(), config=cfg)
    when = datetime(2024, 2, 1, tzinfo=UTC)
    memory.record_episode(
        "technical_analyst", "UPP technicals: trend up", {"d": "LONG"}, created_at=when,
        symbol="UPP",
    )  # fmt: skip
    memory.record_episode(
        "technical_analyst", "DWN technicals: trend down", {"d": "SHORT"}, created_at=when,
        symbol="DWN",
    )  # fmt: skip
    fake = FakeLLM({"technical_analyst": _report()})  # any step (the repair too)
    agent = TechnicalAnalyst(LLMGateway(fake, CONFIG), config=CONFIG, traces=TraceStore())
    agent.run(AgentContext(tworld.view(), memory=memory, inputs={"symbol": "UPP"}))
    user = fake.calls[0].messages[1].content
    assert "UPP technicals: trend up" in user and "DWN technicals" not in user
