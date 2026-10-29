"""Stage 10 Task 5: market series (yields, spreads, VIX) are available obs_date + 1 day 06:00 ET.

The brief's rule was "obs_date + 1 day 08:00 ET"; with the strict cutoff at the 08:00 decision
that meant day D was first used on D + 2. The owner approved moving the default to 06:00 so D's
close is used at the D + 1 decision (Stage 10 follow-up, Decisions log 2026-10-29).
"""

from datetime import UTC, date, datetime, time, timedelta

import pandas as pd
from macro_helpers import DGS10, make_rows, stored_rows

from atlas.core.config import MacroConfig
from atlas.core.pit import AsOf
from atlas.data.macro import attach_available_at, macro_pit

LONG = timedelta(days=400)


def dgs10_at(obs: date, config: MacroConfig | None = None) -> pd.Timestamp:
    rows = stored_rows(DGS10, config)
    return rows.loc[rows["obs_date"] == obs, "available_at"].iloc[0]


def test_market_first_vintage_available_next_day_0600_et() -> None:
    assert dgs10_at(date(2020, 3, 2)) == pd.Timestamp("2020-03-03 11:00", tz="UTC")  # 06:00 EST
    # Friday's value: the next calendar day (Saturday) 06:00, though FRED posted it on Monday.
    assert dgs10_at(date(2020, 4, 17)) == pd.Timestamp("2020-04-18 10:00", tz="UTC")


def test_market_available_at_follows_dst() -> None:
    assert dgs10_at(date(2020, 3, 6)) == pd.Timestamp("2020-03-07 11:00", tz="UTC")  # EST
    assert dgs10_at(date(2020, 3, 9)) == pd.Timestamp("2020-03-10 10:00", tz="UTC")  # EDT


def test_market_rule_is_configurable() -> None:
    config = MacroConfig(market_lag_days=2, market_available_time=time(6, 30))
    assert dgs10_at(date(2020, 3, 2), config) == pd.Timestamp("2020-03-04 11:30", tz="UTC")


def test_market_revision_waits_for_its_vintage() -> None:
    raw = make_rows(
        ("DGS10", date(2020, 3, 2), 1.10, date(2020, 3, 3), datetime(2000, 1, 1, tzinfo=UTC)),
        ("DGS10", date(2020, 3, 2), 1.12, date(2020, 3, 20), datetime(2000, 1, 1, tzinfo=UTC)),
        ("DGS10", date(2020, 3, 5), 0.92, date(2020, 3, 5), datetime(2000, 1, 1, tzinfo=UTC)),
    ).drop(columns="available_at")
    out = attach_available_at(raw, DGS10, MacroConfig())
    assert out["available_at"].tolist() == [
        pd.Timestamp("2020-03-03 11:00", tz="UTC"),  # first vintage: obs + 1 day
        pd.Timestamp("2020-03-20 10:00", tz="UTC"),  # revision: its own vintage date, 06:00 EDT
        pd.Timestamp("2020-03-06 11:00", tz="UTC"),  # first vintage, even if dated the same day
    ]


def test_dgs10_used_at_next_morning_decision() -> None:
    rows = stored_rows(DGS10)
    on_02 = macro_pit(rows, ["DGS10"], AsOf.for_session(date(2020, 3, 2)), LONG)
    assert pd.Timestamp("2020-03-02") not in on_02.index  # the day's own close: not yet
    on_03 = macro_pit(rows, ["DGS10"], AsOf.for_session(date(2020, 3, 3)), LONG)
    assert on_03.loc[pd.Timestamp("2020-03-02"), "DGS10"] == 1.10  # D + 1, 08:00 decision
    assert on_03.index.max() == pd.Timestamp("2020-03-02")


def test_market_time_at_the_cutoff_is_not_known() -> None:
    # Strict cutoff: a value available exactly at the 08:00 decision time is not used then.
    rows = stored_rows(DGS10, MacroConfig(market_available_time=time(8, 0)))
    on_03 = macro_pit(rows, ["DGS10"], AsOf.for_session(date(2020, 3, 3)), LONG)
    assert pd.Timestamp("2020-03-02") not in on_03.index
    on_04 = macro_pit(rows, ["DGS10"], AsOf.for_session(date(2020, 3, 4)), LONG)
    assert on_04.loc[pd.Timestamp("2020-03-02"), "DGS10"] == 1.10


def test_good_friday_has_no_row() -> None:
    frame = macro_pit(stored_rows(DGS10), ["DGS10"], AsOf.for_session(date(2020, 4, 20)), LONG)
    assert pd.Timestamp("2020-04-10") not in frame.index
    assert frame.loc[pd.Timestamp("2020-04-09"), "DGS10"] == 0.73
    assert frame.loc[pd.Timestamp("2020-04-13"), "DGS10"] == 0.76
