"""Stage 10 Task 5: market series (yields, spreads, VIX) are available obs_date + 1 day 08:00 ET."""

from datetime import UTC, date, datetime, timedelta

import pandas as pd
from macro_helpers import DGS10, make_rows, stored_rows

from atlas.core.config import MacroConfig
from atlas.core.pit import AsOf
from atlas.data.macro import attach_available_at, macro_pit

LONG = timedelta(days=400)


def dgs10_at(obs: date) -> pd.Timestamp:
    rows = stored_rows(DGS10)
    return rows.loc[rows["obs_date"] == obs, "available_at"].iloc[0]


def test_market_first_vintage_available_next_day_0800_et() -> None:
    assert dgs10_at(date(2020, 3, 2)) == pd.Timestamp("2020-03-03 13:00", tz="UTC")  # 08:00 EST
    # Friday's value: the next calendar day (Saturday) 08:00, though FRED posted it on Monday.
    assert dgs10_at(date(2020, 4, 17)) == pd.Timestamp("2020-04-18 12:00", tz="UTC")


def test_market_available_at_follows_dst() -> None:
    assert dgs10_at(date(2020, 3, 6)) == pd.Timestamp("2020-03-07 13:00", tz="UTC")  # EST
    assert dgs10_at(date(2020, 3, 9)) == pd.Timestamp("2020-03-10 12:00", tz="UTC")  # EDT


def test_market_rule_is_configurable() -> None:
    config = MacroConfig(market_lag_days=2, market_available_time="06:30")  # type: ignore[arg-type]
    rows = stored_rows(DGS10, config)
    first = rows.loc[rows["obs_date"] == date(2020, 3, 2), "available_at"].iloc[0]
    assert first == pd.Timestamp("2020-03-04 11:30", tz="UTC")


def test_market_revision_waits_for_its_vintage() -> None:
    raw = make_rows(
        ("DGS10", date(2020, 3, 2), 1.10, date(2020, 3, 3), datetime(2000, 1, 1, tzinfo=UTC)),
        ("DGS10", date(2020, 3, 2), 1.12, date(2020, 3, 20), datetime(2000, 1, 1, tzinfo=UTC)),
        ("DGS10", date(2020, 3, 5), 0.92, date(2020, 3, 5), datetime(2000, 1, 1, tzinfo=UTC)),
    ).drop(columns="available_at")
    out = attach_available_at(raw, DGS10, MacroConfig())
    assert out["available_at"].tolist() == [
        pd.Timestamp("2020-03-03 13:00", tz="UTC"),  # first vintage: obs + 1 day
        pd.Timestamp("2020-03-20 12:00", tz="UTC"),  # revision: its own vintage date, 08:00 EDT
        pd.Timestamp("2020-03-06 13:00", tz="UTC"),  # first vintage, even if dated the same day
    ]


def test_dgs10_pit_strict_cutoff() -> None:
    rows = stored_rows(DGS10)
    on_03 = macro_pit(rows, ["DGS10"], AsOf.for_session(date(2020, 3, 3)), LONG)
    assert pd.Timestamp("2020-03-02") not in on_03.index  # 08:00 is not before the 08:00 cutoff
    on_04 = macro_pit(rows, ["DGS10"], AsOf.for_session(date(2020, 3, 4)), LONG)
    assert on_04.loc[pd.Timestamp("2020-03-02"), "DGS10"] == 1.10
    assert on_04.index.max() == pd.Timestamp("2020-03-02")


def test_good_friday_has_no_row() -> None:
    frame = macro_pit(stored_rows(DGS10), ["DGS10"], AsOf.for_session(date(2020, 4, 20)), LONG)
    assert pd.Timestamp("2020-04-10") not in frame.index
    assert frame.loc[pd.Timestamp("2020-04-09"), "DGS10"] == 0.73
    assert frame.loc[pd.Timestamp("2020-04-13"), "DGS10"] == 0.76
