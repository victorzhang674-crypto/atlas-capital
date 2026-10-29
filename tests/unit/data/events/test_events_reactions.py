"""Stage 12 Task 5: the price reaction after each earnings event (point in time)."""

from datetime import date
from typing import Any

import pandas as pd
import pytest
from events_helpers import et, event_row, frame, utc

from atlas.core.calendar import TradingCalendar
from atlas.core.pit import AsOf, bar_available_at
from atlas.core.storage import Lake
from atlas.data.events import EVENT_COLUMNS, EVENTS, normalize_events
from atlas.data.events.reactions import (
    EARNINGS_REACTIONS,
    REACTION_COLUMNS,
    compute_reactions,
    ingest_earnings_reactions,
    reaction_window,
)
from atlas.data.prices import CORPORATE_ACTIONS, PRICES_DAILY

CAL = TradingCalendar()
DP, D0, D1, DN = date(2023, 8, 3), date(2023, 8, 4), date(2023, 8, 7), date(2023, 8, 8)
EVENT = event_row()  # AAPL 8-K accepted Thursday 2023-08-03 16:30 ET


def bars(symbol: str, prices: dict[date, tuple[float, float]]) -> pd.DataFrame:
    """Daily bars (`prices_daily` columns) from {session: (open, close)}."""
    rows = [
        {
            "symbol": symbol,
            "date": d,
            "open": o,
            "high": max(o, c),
            "low": min(o, c),
            "close": c,
            "adj_close": c,
            "volume": 1_000_000,
            "vendor": "test",
            "available_at": bar_available_at(d, CAL),
        }
        for d, (o, c) in prices.items()
    ]
    out = pd.DataFrame(rows)
    out["available_at"] = pd.to_datetime(out["available_at"], utc=True).dt.as_unit("us")
    return out


def actions(rows: list[tuple[str, date, str, float | None, float | None]]) -> pd.DataFrame:
    """Corporate actions from (symbol, ex_date, type, ratio, amount) rows."""
    out = pd.DataFrame(
        [
            {"symbol": s, "ex_date": d, "type": t, "ratio": r, "amount": a, "vendor": "test",
             "available_at": bar_available_at(d, CAL)}
            for s, d, t, r, a in rows
        ],
        columns=list(CORPORATE_ACTIONS.column_names),
    )  # fmt: skip
    out["available_at"] = pd.to_datetime(out["available_at"], utc=True).dt.as_unit("us")
    out[["ratio", "amount"]] = out[["ratio", "amount"]].astype("float64")
    return out


STOCK = {DP: (99.0, 100.0), D0: (110.0, 121.0), D1: (122.0, 125.0), DN: (126.0, 130.0)}
SPY = {DP: (99.0, 100.0), D0: (100.0, 101.0), D1: (101.0, 101.5), DN: (101.5, 102.0)}


def _daily(stock: dict[date, tuple[float, float]] | None = None) -> pd.DataFrame:
    return pd.concat([bars("AAPL", stock or STOCK), bars("SPY", SPY)], ignore_index=True)


def _events(*rows: dict[str, Any]) -> pd.DataFrame:
    return normalize_events(frame(list(rows) or [EVENT], EVENT_COLUMNS))


def _compute(
    events: pd.DataFrame, daily: pd.DataFrame, acts: pd.DataFrame | None = None
) -> tuple[pd.DataFrame, int]:
    return compute_reactions(
        events,
        daily,
        acts if acts is not None else actions([]),
        CAL,
        reaction_days=3,
        benchmark="SPY",
    )


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (et(2023, 8, 3, 16, 30), (DP, D0, DN)),  # after the close: next session
        (et(2023, 8, 3, 7, 0), (date(2023, 8, 2), DP, date(2023, 8, 7))),  # before the open
        (et(2023, 8, 3, 11, 0), (date(2023, 8, 2), DP, date(2023, 8, 7))),  # in session
        (et(2023, 8, 3, 16, 0), (DP, D0, DN)),  # at the close: next session
        (et(2023, 8, 5, 10, 0), (D0, D1, DN + pd.Timedelta(days=1))),  # Saturday: Monday
    ],
)
def test_reaction_window_amc_bmo_weekend(instant: Any, expected: tuple[date, ...]) -> None:
    got = reaction_window(instant, CAL, 3)
    assert got == tuple(d if isinstance(d, date) else d.date() for d in expected)


def test_reaction_values() -> None:
    out, skipped = _compute(_events(), _daily())
    assert skipped == 0 and tuple(out.columns) == REACTION_COLUMNS
    row = out.iloc[0]
    assert (row["symbol"], row["event_id"], row["benchmark"]) == ("AAPL", EVENT["event_id"], "SPY")
    assert (row["reaction_date"], row["window_end"]) == (D0, DN)
    assert row["gap_return"] == pytest.approx(0.10)
    assert row["oc_return"] == pytest.approx(0.10)
    assert row["oc_abnormal"] == pytest.approx(0.10 - 0.01)
    assert row["car"] == pytest.approx(0.30 - 0.02)


def test_reaction_adjusts_split_in_window() -> None:
    split = {**STOCK, DP: (198.0, 200.0)}  # 2-for-1 split effective on D0
    out, _ = _compute(_events(), _daily(split), actions([("AAPL", D0, "split", 2.0, None)]))
    assert out.iloc[0]["gap_return"] == pytest.approx(0.10)
    assert out.iloc[0]["car"] == pytest.approx(0.28)


def test_reaction_adjusts_dividend_in_window() -> None:
    paid = {DP: (99.0, 100.0), D0: (108.9, 119.79), D1: (120.0, 125.0), DN: (126.0, 128.7)}
    out, _ = _compute(_events(), _daily(paid), actions([("AAPL", D0, "dividend", None, 1.0)]))
    assert out.iloc[0]["gap_return"] == pytest.approx(0.10)
    assert out.iloc[0]["oc_return"] == pytest.approx(0.10)
    assert out.iloc[0]["car"] == pytest.approx(0.28)


def test_reaction_ignores_action_outside_window() -> None:
    out, _ = _compute(
        _events(), _daily(), actions([("AAPL", date(2023, 8, 1), "split", 4.0, None)])
    )
    assert out.iloc[0]["car"] == pytest.approx(0.28)


def test_reaction_available_after_window_closes() -> None:
    out, _ = _compute(_events(), _daily())
    available = out.iloc[0]["available_at"].to_pydatetime()
    assert available == bar_available_at(DN, CAL)  # 2023-08-08 16:15 ET
    assert not AsOf.for_session(DN).knows(available)
    assert AsOf.for_session(date(2023, 8, 9)).knows(available)


def test_missing_bar_skipped() -> None:
    gap = {d: v for d, v in STOCK.items() if d != D1}
    out, skipped = _compute(_events(), _daily(gap))
    assert out.empty and skipped == 1
    no_spy = bars("AAPL", STOCK)
    out, skipped = _compute(_events(), no_spy)
    assert out.empty and skipped == 1


def test_open_window_skipped() -> None:
    late = event_row(
        event_id="sec-late", event_date=date(2023, 8, 7), event_time=utc(2023, 8, 7, 20, 30),
        available_at=utc(2023, 8, 7, 20, 30),
    )  # fmt: skip
    out, skipped = _compute(_events(EVENT, late), _daily())
    assert out["event_id"].tolist() == [EVENT["event_id"]] and skipped == 1


def test_only_actual_events_used() -> None:
    scheduled = event_row(
        event_id="yahoo-AAPL-2023-08-03", event_time=None, time_of_day="unknown",
        source="yahoo", available_at=utc(2023, 7, 1),
    )  # fmt: skip
    out, skipped = _compute(_events(EVENT, scheduled), _daily())
    assert out["event_id"].tolist() == [EVENT["event_id"]] and skipped == 0


def _seed(lake: Lake, acts: pd.DataFrame | None = None) -> None:
    lake.write_table(EVENTS.name, _events(), mode="upsert")
    lake.write_table(PRICES_DAILY.name, _daily(), mode="upsert")
    if acts is not None:
        lake.write_table(CORPORATE_ACTIONS.name, acts, mode="upsert")


def test_ingest_reactions_from_lake(tmp_lake: Lake) -> None:
    _seed(tmp_lake, actions([("AAPL", date(2023, 8, 1), "dividend", None, 0.24)]))
    report = ingest_earnings_reactions(tmp_lake, ["AAPL"])
    assert (report.rows, report.skipped) == (1, 0)
    stored = tmp_lake.read_table(EARNINGS_REACTIONS.name)
    assert stored.iloc[0]["car"] == pytest.approx(0.28)


def test_ingest_reactions_all_symbols_and_idempotent(tmp_lake: Lake) -> None:
    _seed(tmp_lake)
    ingest_earnings_reactions(tmp_lake)
    first = tmp_lake.read_table(EARNINGS_REACTIONS.name)
    ingest_earnings_reactions(tmp_lake)
    pd.testing.assert_frame_equal(tmp_lake.read_table(EARNINGS_REACTIONS.name), first)
    assert len(first) == 1


def test_ingest_reactions_without_events(tmp_lake: Lake) -> None:
    report = ingest_earnings_reactions(tmp_lake, ["AAPL"])
    assert (report.rows, report.skipped) == (0, 0)
    assert not tmp_lake.has_table(EARNINGS_REACTIONS.name)


def test_reaction_table_declaration() -> None:
    assert EARNINGS_REACTIONS.column_names == REACTION_COLUMNS
    assert EARNINGS_REACTIONS.primary_key == ("symbol", "event_id")


def test_bad_vendor_action_skips_only_that_event() -> None:
    msft = event_row(event_id="sec-msft", symbol="MSFT")
    daily = pd.concat([_daily(), bars("MSFT", STOCK)], ignore_index=True)
    broken = actions([("MSFT", D0, "dividend", None, 500.0)])  # above the prior close
    out, skipped = _compute(_events(EVENT, msft), daily, broken)
    assert out["event_id"].tolist() == [EVENT["event_id"]] and skipped == 1
