"""Stage 12 Task 9: point-in-time event queries (`upcoming_events`, `past_events`, reactions)."""

from datetime import date, datetime
from typing import Any

import pandas as pd
import pytest
from events_helpers import REPO_ROOT, event_row, frame, macro_row, utc

from atlas.core.calendar import TradingCalendar
from atlas.core.errors import LookAheadError
from atlas.core.pit import AsOf, bar_available_at
from atlas.core.storage import Lake
from atlas.data.events import (
    EVENT_COLUMNS,
    EVENTS,
    MACRO_EVENT_COLUMNS,
    MACRO_EVENTS,
    normalize_events,
    normalize_macro_events,
)
from atlas.data.events.macro_calendar import fomc_events, load_macro_calendar
from atlas.data.events.pit import Events, dividend_events, past_events, upcoming_events
from atlas.data.events.reactions import EARNINGS_REACTIONS, REACTION_COLUMNS
from atlas.data.prices import CORPORATE_ACTIONS

CAL = TradingCalendar()
EARNINGS = event_row()  # AAPL 8-K 2023-08-03 16:30 ET


def _events(*rows: dict[str, Any]) -> pd.DataFrame:
    return normalize_events(frame(list(rows), EVENT_COLUMNS))


def _macro(*rows: dict[str, Any]) -> pd.DataFrame:
    return normalize_macro_events(frame(list(rows), MACRO_EVENT_COLUMNS))


def _dividend(symbol: str, ex: date, amount: float) -> pd.DataFrame:
    out = pd.DataFrame(
        [{"symbol": symbol, "ex_date": ex, "type": "dividend", "ratio": None, "amount": amount,
          "vendor": "test", "available_at": bar_available_at(ex, CAL)}],
        columns=list(CORPORATE_ACTIONS.column_names),
    )  # fmt: skip
    out["available_at"] = pd.to_datetime(out["available_at"], utc=True).dt.as_unit("us")
    out[["ratio", "amount"]] = out[["ratio", "amount"]].astype("float64")
    return out


def _reaction(event_id: str, reaction_date: date, window_end: date) -> dict[str, Any]:
    return {
        "symbol": "AAPL", "event_id": event_id, "event_time": utc(2023, 8, 3, 20, 30),
        "reaction_date": reaction_date, "window_end": window_end, "gap_return": 0.01,
        "oc_return": 0.02, "oc_abnormal": 0.01, "car": 0.03, "benchmark": "SPY",
        "available_at": bar_available_at(window_end, CAL),
    }  # fmt: skip


def _lake(lake: Lake, *, events: pd.DataFrame | None = None, macro: pd.DataFrame | None = None,
          actions: pd.DataFrame | None = None, reactions: list[dict[str, Any]] | None = None
          ) -> Events:  # fmt: skip
    if events is not None:
        lake.write_table(EVENTS.name, events, mode="upsert")
    if macro is not None:
        lake.write_table(MACRO_EVENTS.name, macro, mode="overwrite")
    if actions is not None:
        lake.write_table(CORPORATE_ACTIONS.name, actions, mode="upsert")
    if reactions:
        out = pd.DataFrame(reactions, columns=list(REACTION_COLUMNS))
        for col in ("event_time", "available_at"):
            out[col] = pd.to_datetime(out[col], utc=True).dt.as_unit("us")
        lake.write_table(EARNINGS_REACTIONS.name, out, mode="upsert")
    return Events(lake)


def test_acceptance_earnings_invisible_before_filing(tmp_lake: Lake) -> None:
    reader = _lake(tmp_lake, events=_events(EARNINGS))
    assert reader.past("AAPL", date(2023, 8, 3)).empty
    assert reader.upcoming(date(2023, 8, 3)).empty  # not known before the filing
    past = reader.past("AAPL", date(2023, 8, 4))
    assert past["event_id"].tolist() == [EARNINGS["event_id"]]
    assert tuple(past.columns) == EVENT_COLUMNS


def test_upcoming_includes_macro_and_scheduled(tmp_lake: Lake) -> None:
    scheduled = event_row(
        event_id="yahoo-AAPL-2024-01-25", event_date=date(2024, 1, 25), event_time=None,
        time_of_day="unknown", source="yahoo", available_at=utc(2024, 1, 2, 12),
    )  # fmt: skip
    reader = _lake(tmp_lake, events=_events(scheduled), macro=_macro(macro_row()))
    out = reader.upcoming(date(2024, 1, 11), horizon_days=14)
    assert out["event_id"].tolist() == ["cpi-2024-01-11", "yahoo-AAPL-2024-01-25"]
    cpi = out.iloc[0]
    assert cpi["symbol"] is None and cpi["time_of_day"] == "BMO"  # 08:30 ET, after the cutoff
    assert reader.upcoming(date(2024, 1, 11), horizon_days=13)["event_id"].tolist() == [
        "cpi-2024-01-11"
    ]
    later = AsOf(day=date(2024, 1, 11), cutoff=utc(2024, 1, 11, 14))  # 09:00 ET: CPI is out
    assert "cpi-2024-01-11" not in set(reader.upcoming(later)["event_id"])


def test_upcoming_horizon_bounds(tmp_lake: Lake) -> None:
    rows = [macro_row(event_id=f"cpi-2024-01-{d:02d}", event_date=date(2024, 1, d),
                      event_time=utc(2024, 1, d, 13, 30)) for d in (10, 11, 25, 26)]  # fmt: skip
    reader = _lake(tmp_lake, macro=_macro(*rows))
    out = reader.upcoming(date(2024, 1, 11), horizon_days=14)
    assert out["event_id"].tolist() == ["cpi-2024-01-11", "cpi-2024-01-25"]


def test_cancelled_fomc_visible_until_cancelled(tmp_lake: Lake) -> None:
    reader = _lake(tmp_lake, macro=fomc_events(load_macro_calendar(
        REPO_ROOT / "config" / "macro_calendar.yaml")))  # fmt: skip
    before = reader.upcoming(date(2020, 3, 10), horizon_days=10)["event_id"].tolist()
    assert before == ["fomc-2020-03-18"]
    after = reader.upcoming(date(2020, 3, 16), horizon_days=10)["event_id"].tolist()
    assert after == []
    assert reader.upcoming(date(2020, 3, 13), horizon_days=10)["event_id"].tolist() == [
        "fomc-2020-03-18"  # the Sunday meeting was unknown on Friday
    ]
    assert "fomc-2020-03-15" not in set(
        reader.upcoming(date(2020, 3, 13), horizon_days=10)["event_id"]
    )


def test_scheduled_latest_snapshot_wins(tmp_lake: Lake) -> None:
    first = event_row(event_id="yahoo-AAPL-2024-01-30", event_date=date(2024, 1, 30),
                      event_time=None, time_of_day="unknown", source="yahoo",
                      available_at=utc(2024, 1, 2, 12))  # fmt: skip
    moved = {**first, "event_id": "yahoo-AAPL-2024-02-01", "event_date": date(2024, 2, 1),
             "available_at": utc(2024, 1, 9, 12)}  # fmt: skip
    reader = _lake(tmp_lake, events=_events(first, moved))
    assert reader.upcoming(date(2024, 1, 5), horizon_days=30)["event_id"].tolist() == [
        "yahoo-AAPL-2024-01-30"
    ]
    assert reader.upcoming(date(2024, 1, 10), horizon_days=30)["event_id"].tolist() == [
        "yahoo-AAPL-2024-02-01"
    ]


def test_scheduled_dropped_after_actual_event(tmp_lake: Lake) -> None:
    scheduled = event_row(event_id="yahoo-AAPL-2023-08-04", event_date=date(2023, 8, 4),
                          event_time=None, time_of_day="unknown", source="yahoo",
                          available_at=utc(2023, 7, 1, 12))  # fmt: skip
    earlier = event_row(
        event_id="sec-old",
        event_date=date(2023, 5, 4),
        event_time=utc(2023, 5, 4, 20, 30),
        available_at=utc(2023, 5, 4, 20, 30),
    )
    reader = _lake(tmp_lake, events=_events(scheduled, earlier, EARNINGS))
    assert reader.upcoming(date(2023, 8, 3))["event_id"].tolist() == ["yahoo-AAPL-2023-08-04"]
    assert reader.upcoming(date(2023, 8, 4)).empty  # the 8-K came a day early


def test_upcoming_symbols_filter_keeps_macro(tmp_lake: Lake) -> None:
    msft = event_row(event_id="yahoo-MSFT-2024-01-25", symbol="MSFT", event_date=date(2024, 1, 25),
                     event_time=None, time_of_day="unknown", source="yahoo",
                     available_at=utc(2024, 1, 2, 12))  # fmt: skip
    reader = _lake(tmp_lake, events=_events(msft), macro=_macro(macro_row()))
    out = reader.upcoming(date(2024, 1, 11), symbols=["AAPL"])
    assert out["event_id"].tolist() == ["cpi-2024-01-11"]


def test_past_events_newest_first_n(tmp_lake: Lake) -> None:
    older = event_row(
        event_id="sec-old",
        event_date=date(2023, 5, 4),
        event_time=utc(2023, 5, 4, 20, 30),
        available_at=utc(2023, 5, 4, 20, 30),
    )
    scheduled = event_row(event_id="yahoo-AAPL-2023-08-01", event_date=date(2023, 8, 1),
                          event_time=None, time_of_day="unknown", source="yahoo",
                          available_at=utc(2023, 7, 1))  # fmt: skip
    reader = _lake(
        tmp_lake,
        events=_events(older, scheduled, EARNINGS),
        actions=_dividend("AAPL", date(2023, 8, 11), 0.24),
    )
    out = reader.past("aapl", date(2023, 8, 14), n=8)
    assert out["event_id"].tolist() == ["div-AAPL-2023-08-11", EARNINGS["event_id"], "sec-old"]
    assert out["event_type"].tolist() == ["ex_dividend", "earnings", "earnings"]
    assert reader.past("AAPL", date(2023, 8, 14), n=1)["event_id"].tolist() == [
        "div-AAPL-2023-08-11"
    ]


def test_dividend_known_from_ex_date_bar(tmp_lake: Lake) -> None:
    reader = _lake(tmp_lake, actions=_dividend("AAPL", date(2023, 8, 11), 0.24))
    assert reader.past("AAPL", date(2023, 8, 11)).empty
    assert reader.upcoming(date(2023, 8, 10)).empty  # ex-dates are known only once seen
    assert len(reader.past("AAPL", date(2023, 8, 14))) == 1


def test_dividend_events_frame() -> None:
    out = dividend_events(_dividend("AAPL", date(2023, 8, 11), 0.24))
    row = out.iloc[0]
    assert (row["event_id"], row["source"], row["time_of_day"]) == (
        "div-AAPL-2023-08-11", "corporate_actions", "unknown",
    )  # fmt: skip
    assert pd.isna(row["event_time"])


def test_reactions_known_after_window(tmp_lake: Lake) -> None:
    rows = [
        _reaction(f"sec-{i}", date(2023, 8, 4), date(2023, 8, 8)) if i == 9
        else _reaction(f"sec-{i}", date(2021 + i // 4, 1 + 3 * (i % 4), 4),
                       date(2021 + i // 4, 1 + 3 * (i % 4), 6))
        for i in range(10)
    ]  # fmt: skip
    reader = _lake(tmp_lake, reactions=rows)
    known = reader.reactions("AAPL", date(2023, 8, 8))
    assert len(known) == 8 and "sec-9" not in set(known["event_id"])
    assert list(known["reaction_date"]) == sorted(known["reaction_date"], reverse=True)
    after = reader.reactions("AAPL", date(2023, 8, 9), n=1)
    assert after["event_id"].tolist() == ["sec-9"]
    assert tuple(after.columns) == REACTION_COLUMNS


@pytest.mark.parametrize("method", ["upcoming", "past", "reactions"])
def test_events_reader_guard_raises_if_filter_bypassed(
    tmp_lake: Lake, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    future = event_row(
        event_id="sec-future",
        event_date=date(2023, 9, 1),
        event_time=utc(2023, 9, 1, 20, 30),
        available_at=utc(2023, 9, 1, 20, 30),
    )
    reader = _lake(
        tmp_lake,
        events=_events(future),
        macro=_macro(macro_row(available_at=utc(2023, 9, 1))),
        reactions=[_reaction("sec-x", date(2023, 9, 1), date(2023, 9, 6))],
    )  # fmt: skip
    monkeypatch.setattr("atlas.data.events.pit.pit_filter", lambda df, as_of, *a: df.copy())
    call = {
        "upcoming": lambda: reader.upcoming(date(2023, 8, 1)),
        "past": lambda: reader.past("AAPL", date(2023, 8, 1)),
        "reactions": lambda: reader.reactions("AAPL", date(2023, 8, 1)),
    }[method]
    with pytest.raises(LookAheadError):
        call()


def test_future_rows_change_nothing(tmp_lake: Lake, tmp_path: Any) -> None:
    base = _events(EARNINGS)
    reader = _lake(tmp_lake, events=base, macro=_macro(macro_row()))
    point = date(2023, 8, 7)
    before = (reader.upcoming(point, horizon_days=200), reader.past("AAPL", point))
    late = event_row(
        event_id="sec-late",
        event_date=date(2023, 8, 10),
        event_time=utc(2023, 8, 10, 20, 30),
        available_at=utc(2023, 8, 10, 20, 30),
    )
    scheduled = event_row(event_id="yahoo-AAPL-2023-11-02", event_date=date(2023, 11, 2),
                          event_time=None, time_of_day="unknown", source="yahoo",
                          available_at=utc(2023, 8, 7, 12, 1))  # fmt: skip
    tmp_lake.write_table(EVENTS.name, _events(late, scheduled), mode="upsert")
    after = (reader.upcoming(point, horizon_days=200), reader.past("AAPL", point))
    pd.testing.assert_frame_equal(after[0], before[0])
    pd.testing.assert_frame_equal(after[1], before[1])


def test_events_reader_empty_lake(tmp_lake: Lake) -> None:
    reader = Events(tmp_lake)
    assert reader.upcoming(date(2024, 1, 2)).empty
    assert reader.past("AAPL", date(2024, 1, 2)).empty
    assert reader.reactions("AAPL", date(2024, 1, 2)).empty


def test_events_reader_argument_errors(tmp_lake: Lake) -> None:
    reader = Events(tmp_lake)
    with pytest.raises(TypeError):
        reader.past("AAPL", datetime(2024, 1, 2, 9))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="horizon_days"):
        reader.upcoming(date(2024, 1, 2), horizon_days=0)
    with pytest.raises(ValueError, match="n must"):
        reader.past("AAPL", date(2024, 1, 2), n=0)


def test_frame_level_functions(tmp_lake: Lake) -> None:
    view = Events(_lake(tmp_lake, events=_events(EARNINGS))._lake).view()
    point = AsOf.for_session(date(2023, 8, 4))
    assert past_events(view, "AAPL", point, 5)["event_id"].tolist() == [EARNINGS["event_id"]]
    assert upcoming_events(view, point, 14, scheduled_match_days=10).empty
