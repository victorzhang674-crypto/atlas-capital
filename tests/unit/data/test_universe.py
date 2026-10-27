"""Stage 08: S&P 500 reference data, membership history, instruments and `Universe`."""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx
from price_helpers import FakePriceProvider, daily_rows
from pydantic import ValidationError
from universe_helpers import changes, changes_html, constituents, constituents_html

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig, ReferenceDataConfig
from atlas.core.errors import ConfigError, DataError
from atlas.core.pit import AsOf, pit_filter
from atlas.core.storage import Lake
from atlas.core.types.instruments import AssetClass, Instrument
from atlas.data.prices import ingest_prices
from atlas.data.reference import (
    INSTRUMENTS,
    TickerChange,
    TickerMap,
    build_instruments,
    fetch_sp500_pages,
    instruments_frame,
    load_ticker_map,
    parse_changes,
    parse_constituents,
    read_instruments,
)
from atlas.data.universe import (
    SP500_INCEPTION,
    MembershipAnomaly,
    MembershipBuild,
    Universe,
    build_membership,
    members_on,
    refresh_universe_reference,
)

# --- Task 1: Wikipedia parsers -----------------------------------------------------------------


def test_parse_constituents_fixture() -> None:
    df = parse_constituents(constituents_html())
    assert list(df.columns) == ["symbol", "name", "sector", "industry", "exchange", "date_added"]
    assert len(df) == 503
    assert df["symbol"].is_unique
    assert list(df["symbol"]) == sorted(df["symbol"])
    by = df.set_index("symbol")
    assert by.loc["MMM", "name"] == "3M"
    assert by.loc["MMM", "sector"] == "Industrials"
    assert by.loc["MMM", "exchange"] == "XNYS"
    assert by.loc["AAPL", "exchange"] == "XNAS"
    assert by.loc["CBOE", "exchange"] == "BATS"
    assert by.loc["TSLA", "date_added"] == date(2020, 12, 21)
    assert "BRK.B" in by.index
    assert set(df["exchange"]) == {"XNYS", "XNAS", "BATS"}


def test_parse_changes_fixture() -> None:
    df = parse_changes(changes_html())
    assert list(df.columns) == ["date", "added", "added_name", "removed", "removed_name", "reason"]
    assert len(df) == 412
    assert df["date"].iloc[0] == date(2026, 10, 6)
    assert df["date"].iloc[-1] == date(1976, 7, 1)
    assert list(df["date"]) == sorted(df["date"], reverse=True)
    tsla = df[df["date"] == date(2020, 12, 21)]
    assert (tsla["added"] == "TSLA").any()
    assert tsla.loc[tsla["added"] == "TSLA", "removed"].item() == "AIV"
    fb = df[df["date"] == date(2013, 12, 23)]
    assert "FB" in fb["added"].tolist()  # with ADS and MHK the same day
    one_sided = df[df["date"] == date(2026, 10, 6)]
    assert one_sided["added"].isna().sum() == 1  # CTVA removed with no addition


def test_parse_changes_cleans_ticker_junk() -> None:
    df = parse_changes(changes_html())
    row = df[df["date"] == date(2013, 12, 2)]
    assert row["added"].tolist() == ["ALLE"]
    assert row["removed"].tolist() == ["JCP"]
    tickers = set(df["added"].dropna()) | set(df["removed"].dropna())
    assert all(" " not in t and "|" not in t for t in tickers)


def test_parse_missing_table_raises() -> None:
    with pytest.raises(DataError, match="constituents"):
        parse_constituents("<html><body><table id='other'><tr><td>x</td></tr></table></html>")
    with pytest.raises(DataError, match="changes"):
        parse_changes(constituents_html())


def test_parse_changes_bad_date_raises() -> None:
    html = changes_html().replace("October 6, 2026", "Octobre 6, 2026", 1)
    with pytest.raises(DataError, match="Octobre 6, 2026"):
        parse_changes(html)


# --- Task 2: ticker-change mapping ---------------------------------------------------------------


def _change(old: str, new: str, effective: date, since: date | None = None) -> TickerChange:
    return TickerChange(old=old, new=new, effective=effective, since=since, source="test")


def test_ticker_map_fb_to_meta() -> None:
    tm = TickerMap([_change("FB", "META", date(2022, 6, 9))])
    assert tm.canonical("FB", date(2013, 12, 23)) == "META"
    assert tm.canonical("fb", date(2022, 6, 8)) == "META"
    assert tm.canonical("META", date(2024, 1, 2)) == "META"
    assert tm.canonical("AAPL", date(2024, 1, 2)) == "AAPL"
    assert tm.historical("META", date(2015, 1, 2)) == "FB"
    assert tm.historical("META", date(2022, 6, 9)) == "META"


def test_ticker_map_follows_chains() -> None:
    tm = TickerMap(
        [_change("ANTM", "ELV", date(2022, 6, 28)), _change("WLP", "ANTM", date(2014, 12, 3))]
    )
    assert tm.canonical("WLP", date(2010, 1, 4)) == "ELV"
    assert tm.canonical("ANTM", date(2018, 1, 2)) == "ELV"
    assert tm.historical("ELV", date(2010, 1, 4)) == "WLP"
    assert tm.historical("ELV", date(2018, 1, 2)) == "ANTM"
    assert tm.historical("ELV", date(2023, 1, 3)) == "ELV"


def test_ticker_map_respects_reuse() -> None:
    tm = TickerMap([_change("IR", "TT", date(2020, 3, 2))])
    assert tm.canonical("IR", date(2010, 11, 17)) == "TT"  # Ingersoll-Rand plc, now Trane
    assert tm.canonical("IR", date(2020, 3, 2)) == "IR"  # the new Ingersoll Rand Inc.
    assert tm.historical("IR", date(2010, 11, 17)) == "IR"


def test_ticker_map_since_window() -> None:
    tm = TickerMap([_change("Q", "IQV", date(2017, 11, 15), since=date(2016, 10, 3))])
    assert tm.canonical("Q", date(2011, 3, 30)) == "Q"  # Qwest
    assert tm.canonical("Q", date(2017, 8, 29)) == "IQV"  # QuintilesIMS
    assert tm.canonical("Q", date(2025, 11, 3)) == "Q"  # Qnity
    assert tm.historical("IQV", date(2017, 1, 3)) == "Q"
    assert tm.historical("IQV", date(2015, 1, 2)) == "IQV"


def test_ticker_map_rename_back_terminates() -> None:
    tm = TickerMap([_change("A1", "B1", date(2021, 1, 4)), _change("B1", "A1", date(2022, 1, 3))])
    assert tm.canonical("A1", date(2019, 1, 2)) == "A1"  # A1 → B1 (2021) → A1 (2022)
    assert tm.canonical("B1", date(2021, 6, 1)) == "A1"
    assert tm.historical("A1", date(2021, 6, 1)) == "B1"
    assert tm.historical("A1", date(2019, 1, 2)) == "A1"


def test_ticker_change_validation() -> None:
    with pytest.raises(ValidationError, match="differ"):
        _change("FB", "fb", date(2022, 6, 9))
    with pytest.raises(ValidationError, match="since"):
        _change("Q", "IQV", date(2017, 11, 15), since=date(2017, 11, 15))
    assert _change("brk-b", "BRK.A", date(2020, 1, 2)).old == "BRK.B"


def test_load_ticker_map_repo_file(repo_root: Path) -> None:
    tm = load_ticker_map(repo_root / "config" / "ticker_changes.yaml")
    olds = {c.old for c in tm.changes}
    assert {"FB", "RE", "SATS", "FLT", "PCLN", "CDAY", "DWDP", "UA", "Q", "IR"} <= olds
    assert all(c.source for c in tm.changes)
    assert tm.canonical("FB", date(2019, 6, 3)) == "META"
    assert tm.canonical("WLP", date(2010, 1, 4)) == "ELV"
    assert tm.canonical("DOW", date(2017, 8, 31)) == "DD"  # Dow Chemical → DowDuPont → DuPont


def test_load_ticker_map_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_ticker_map(tmp_path / "none.yaml").changes == ()


@pytest.mark.parametrize(
    ("text", "pattern"),
    [
        (
            "changes:\n  - {old: FB, new: META, effective: 2022-06-09, source: x, n: 1}\n",
            r"changes\.0\.n: Extra",
        ),
        ("changes:\n  - {old: FB, new: FB, effective: 2022-06-09, source: x}\n", "differ"),
        (
            "changes:\n  - {old: FB, new: META, effective: 2022-06-09, source: x}\n"
            "  - {old: FB, new: META, effective: 2022-06-09, source: y}\n",
            "twice",
        ),
        ("other: 1\n", "other"),
        ("changes: [\n", "YAML"),
    ],
)
def test_load_ticker_map_rejects_bad_file(tmp_path: Path, text: str, pattern: str) -> None:
    path = tmp_path / "ticker_changes.yaml"
    path.write_text(text)
    with pytest.raises(ConfigError, match=pattern) as info:
        load_ticker_map(path)
    assert "ticker_changes.yaml" in str(info.value)


# --- Task 3: membership reconstruction ----------------------------------------------------------

D = date


def _intervals(build: MembershipBuild) -> dict[str, list[tuple[date, date | None]]]:
    out: dict[str, list[tuple[date, date | None]]] = {}
    for row in build.intervals.itertuples():
        out.setdefault(row.symbol, []).append((row.start_date, row.end_date))
    return out


def test_reconstruct_add_and_remove() -> None:
    build = build_membership(constituents("A", "B"), changes((D(2020, 1, 2), "B", "C")))
    assert list(build.intervals.columns) == ["index_id", "symbol", "start_date", "end_date"]
    assert set(build.intervals["index_id"]) == {"SP500"}
    assert _intervals(build) == {
        "A": [(SP500_INCEPTION, None)],
        "B": [(D(2020, 1, 2), None)],
        "C": [(SP500_INCEPTION, D(2020, 1, 2))],
    }
    assert build.anomalies == ()
    assert build.renames == 0
    assert members_on(build.intervals, D(2019, 12, 31)) == ["A", "C"]
    assert members_on(build.intervals, D(2020, 1, 2)) == ["A", "B"]


def test_reconstruct_readd_gives_two_intervals() -> None:
    rows = changes((D(2022, 3, 1), "X", "Y"), (D(2020, 1, 2), "Y", "X"), (D(2018, 1, 2), "X", "Z"))
    build = build_membership(constituents("X"), rows)
    got = _intervals(build)
    assert got["X"] == [(D(2018, 1, 2), D(2020, 1, 2)), (D(2022, 3, 1), None)]
    assert got["Y"] == [(D(2020, 1, 2), D(2022, 3, 1))]
    assert got["Z"] == [(SP500_INCEPTION, D(2018, 1, 2))]


def test_rename_row_is_not_a_membership_change() -> None:  # Review focus 2
    tm = TickerMap([_change("OLD1", "NEW1", D(2021, 1, 4))])
    rows = changes((D(2021, 1, 4), "NEW1", "OLD1"), (D(2015, 1, 2), "OLD1", "X"))
    build = build_membership(constituents("NEW1"), rows, ticker_map=tm)
    assert _intervals(build) == {
        "NEW1": [(D(2015, 1, 2), None)],
        "X": [(SP500_INCEPTION, D(2015, 1, 2))],
    }
    assert build.renames == 1
    assert build.anomalies == ()


def test_added_not_member_is_an_anomaly() -> None:
    build = build_membership(constituents("A"), changes((D(2020, 1, 2), "Z", None)))
    assert build.anomalies == (
        MembershipAnomaly(date=D(2020, 1, 2), symbol="Z", kind="added_not_member", detail="Z Inc"),
    )
    assert _intervals(build) == {"A": [(SP500_INCEPTION, None)]}


def test_removed_while_member_is_an_anomaly() -> None:
    build = build_membership(constituents("A"), changes((D(2020, 1, 2), None, "A")))
    assert [(a.symbol, a.kind) for a in build.anomalies] == [("A", "removed_while_member")]
    assert _intervals(build) == {"A": [(SP500_INCEPTION, None)]}


def test_bad_symbol_is_an_anomaly() -> None:  # Review focus 1
    build = build_membership(constituents("A"), changes((D(2020, 1, 2), "B$D", "C")))
    assert [(a.symbol, a.kind) for a in build.anomalies] == [("B$D", "bad_symbol")]
    assert _intervals(build)["C"] == [(SP500_INCEPTION, D(2020, 1, 2))]


def test_current_member_without_add_row_uses_date_added() -> None:
    build = build_membership(
        constituents("A", "B", date_added=D(2019, 3, 4)), changes((D(2020, 1, 2), "B", None))
    )
    assert _intervals(build) == {"A": [(D(2019, 3, 4), None)], "B": [(D(2020, 1, 2), None)]}


def test_date_added_ignored_for_names_in_the_log() -> None:
    # A rename row restarts the table's "Date added" (DuPont); the log's history wins.
    tm = TickerMap([_change("OLD1", "NEW1", D(2021, 1, 4))])
    rows = changes((D(2021, 1, 4), "NEW1", "OLD1"))
    build = build_membership(constituents("NEW1", date_added=D(2021, 1, 4)), rows, ticker_map=tm)
    assert _intervals(build) == {"NEW1": [(SP500_INCEPTION, None)]}


@pytest.fixture(scope="module")
def fixture_build(repo_root: Path) -> MembershipBuild:
    return build_membership(
        parse_constituents(constituents_html()),
        parse_changes(changes_html()),
        ticker_map=load_ticker_map(repo_root / "config" / "ticker_changes.yaml"),
    )


def test_fixture_tsla_added_2020_12_21(fixture_build: MembershipBuild) -> None:
    before = members_on(fixture_build.intervals, D(2020, 12, 18))
    after = members_on(fixture_build.intervals, D(2020, 12, 21))
    assert "TSLA" not in before
    assert "TSLA" in after
    assert "AIV" in before
    assert "AIV" not in after


def test_fixture_fb_history_is_meta(fixture_build: MembershipBuild) -> None:
    got = _intervals(fixture_build)
    assert got["META"] == [(D(2013, 12, 23), None)]
    assert "FB" not in got
    # DD is the Dow Chemical → DowDuPont → DuPont line (DOW → DWDP → DD, both rename rows):
    # one interval; old E.I. du Pont's removal on 2017-09-01 is a reused-ticker anomaly.
    assert got["DD"] == [(SP500_INCEPTION, None)]
    assert "DD" in members_on(fixture_build.intervals, D(2018, 6, 1))
    assert got["DOW"][-1] == (D(2019, 4, 2), None)  # the new Dow Inc.
    assert got["TT"] == [(D(2010, 11, 17), None)]  # Ingersoll-Rand plc, now Trane
    assert got["IR"] == [(D(2020, 3, 2), None)]


def test_fixture_member_counts(fixture_build: MembershipBuild) -> None:
    for day in (D(2018, 1, 2), D(2019, 6, 3), D(2021, 1, 4), D(2024, 1, 2)):
        assert 500 <= len(members_on(fixture_build.intervals, day)) <= 510, day
    assert len(members_on(fixture_build.intervals, D(2026, 10, 26))) == 503


def test_fixture_anomalies_all_before_backtest_start(fixture_build: MembershipBuild) -> None:
    start = AtlasConfig().backtest.start
    assert fixture_build.anomalies  # the log has known errors before 2018
    assert all(a.date < start for a in fixture_build.anomalies), fixture_build.anomalies
    assert fixture_build.renames >= 4  # CDAY→DAY, WLTW→WTW, DWDP→DD, Q→IQV, DOW→DWDP rows


# --- Task 4: instruments, ETF universe and the reference refresh --------------------------------


@pytest.fixture(scope="module")
def repo_ticker_map(repo_root: Path) -> TickerMap:
    return load_ticker_map(repo_root / "config" / "ticker_changes.yaml")


def test_build_instruments_fixture(
    fixture_build: MembershipBuild, repo_ticker_map: TickerMap
) -> None:
    etfs = AtlasConfig().data.universes["macro_etfs"].tickers
    insts = build_instruments(
        parse_constituents(constituents_html()),
        parse_changes(changes_html()),
        fixture_build.intervals,
        etf_symbols=etfs,
        ticker_map=repo_ticker_map,
    )
    by = {i.symbol: i for i in insts}
    assert len(by) == len(insts)
    assert set(fixture_build.intervals["symbol"]) | set(etfs) == set(by)
    aapl = by["AAPL"]
    assert (aapl.asset_class, aapl.sector, aapl.exchange, aapl.is_etf) == (
        AssetClass.EQUITY,
        "Information Technology",
        "XNAS",
        False,
    )
    assert aapl.name == "Apple Inc."
    aiv = by["AIV"]  # removed 2020-12-21: name from the log, no sector or exchange
    assert (aiv.name, aiv.sector, aiv.exchange) == ("Aimco", None, None)
    assert by["META"].name == "Meta Platforms"
    etf_rows = [i for i in insts if i.is_etf]
    assert sorted(i.symbol for i in etf_rows) == sorted(etfs)
    assert len(etf_rows) == 30
    assert all(i.asset_class is AssetClass.ETF and i.sector is None for i in etf_rows)
    assert all(i.is_shortable for i in insts)
    assert all(i.first_trade_date is None for i in insts)  # no prices given


def test_instruments_frame_matches_schema() -> None:
    insts = [
        Instrument(symbol="SPY", asset_class=AssetClass.ETF),
        Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY, name="Apple Inc."),
    ]
    frame = instruments_frame(insts)
    assert list(frame.columns) == list(INSTRUMENTS.column_names)
    assert frame["symbol"].tolist() == ["AAPL", "SPY"]
    assert frame["is_etf"].tolist() == [False, True]
    assert frame["asset_class"].tolist() == ["EQUITY", "ETF"]


def _fake_prices(lake: Lake, rows: dict[str, list[tuple[date, float, int]]]) -> None:
    daily = pd.concat([daily_rows(s, r) for s, r in rows.items()], ignore_index=True)
    start = min(d for r in rows.values() for d, _, _ in r)
    end = max(d for r in rows.values() for d, _, _ in r)
    report = ingest_prices(lake, FakePriceProvider(daily), sorted(rows), start, end)
    assert report.symbols_failed == ()


def test_refresh_writes_tables_and_is_idempotent(
    tmp_lake: Lake, repo_ticker_map: TickerMap
) -> None:
    _fake_prices(tmp_lake, {"AAPL": [(D(2024, 1, 2), 185.6, 1000), (D(2024, 1, 3), 184.3, 900)]})
    report = refresh_universe_reference(
        tmp_lake, constituents_html(), changes_html(), ticker_map=repo_ticker_map
    )
    assert report.renames == 8
    assert len(report.anomalies) == 12
    membership = tmp_lake.read_table("index_membership")
    instruments = tmp_lake.read_table("instruments")
    assert report.intervals == len(membership) == 877  # 858 symbols, some with re-entries
    assert report.instruments == len(instruments) == 858 + 30
    again = refresh_universe_reference(
        tmp_lake, constituents_html(), changes_html(), ticker_map=repo_ticker_map
    )
    assert again == report
    pd.testing.assert_frame_equal(tmp_lake.read_table("index_membership"), membership)
    pd.testing.assert_frame_equal(tmp_lake.read_table("instruments"), instruments)
    by = {i.symbol: i for i in read_instruments(tmp_lake)}
    assert (by["AAPL"].first_trade_date, by["AAPL"].last_trade_date) == (
        D(2024, 1, 2),
        D(2024, 1, 3),
    )
    assert by["MSFT"].first_trade_date is None
    assert by["SPY"].is_etf


def test_refresh_default_ticker_map_comes_from_config(tmp_lake: Lake, tmp_path: Path) -> None:
    path = tmp_path / "renames.yaml"
    path.write_text("changes: []\n")
    cfg = AtlasConfig().model_copy(
        update={
            "data": AtlasConfig().data.model_copy(
                update={"reference": ReferenceDataConfig(ticker_changes_file=path)}
            )
        }
    )
    report = refresh_universe_reference(tmp_lake, constituents_html(), changes_html(), config=cfg)
    assert any(a.symbol == "FB" and a.kind == "added_not_member" for a in report.anomalies)


# --- Task 5: Universe.members and Universe.tradable ---------------------------------------------

CAL = TradingCalendar()
AS_OF = D(2024, 6, 3)  # a Monday; the last known bar at 08:00 is Friday 2024-05-31
SESSIONS = CAL.sessions(CAL.offset(D(2024, 5, 31), -299), D(2024, 5, 31))  # 300 sessions


def _membership(lake: Lake, symbols: Sequence[str]) -> None:
    """`symbols` as SP500 members since 2000-01-03, replacing the membership table."""
    frame = pd.DataFrame(
        {
            "index_id": "SP500",
            "symbol": list(symbols),
            "start_date": D(2000, 1, 3),
            "end_date": pd.Series([None] * len(symbols), dtype=object),
        }
    )
    lake.write_table("index_membership", frame, mode="overwrite")


def _bars(sessions: Sequence[date], close: float, volume: int) -> list[tuple[date, float, int]]:
    return [(d, close, volume) for d in sessions]


@pytest.fixture
def market(tmp_lake: Lake) -> Lake:
    """Members LIQ, CHEAP, THIN, YOUNG, STALE, EDGE, NOPRICE; OUTSIDER is liquid but no member."""
    _membership(tmp_lake, ["LIQ", "CHEAP", "THIN", "YOUNG", "STALE", "EDGE", "NOPRICE"])
    _fake_prices(
        tmp_lake,
        {
            "LIQ": _bars(SESSIONS, 50.0, 1_000_000),  # ADV 50e6
            "CHEAP": _bars(SESSIONS, 4.0, 10_000_000),  # price below 5
            "THIN": _bars(SESSIONS, 50.0, 200_000),  # ADV 10e6
            "YOUNG": _bars(SESSIONS[-200:], 50.0, 1_000_000),  # 200 bars of history
            "STALE": _bars(SESSIONS[:-6], 50.0, 1_000_000),  # last bar 6 sessions back
            "EDGE": _bars(SESSIONS[:-5], 50.0, 1_000_000),  # last bar 5 sessions back
            "OUTSIDER": _bars(SESSIONS, 50.0, 1_000_000),
        },
    )
    return tmp_lake


def test_members_static_universe(tmp_lake: Lake) -> None:
    universe = Universe(tmp_lake)
    etfs = sorted(AtlasConfig().data.universes["macro_etfs"].tickers)
    assert universe.members(D(2019, 6, 3), "macro_etfs") == etfs
    assert universe.members(AsOf.for_session(AS_OF), "macro_etfs") == etfs


def test_members_index_universe(tmp_lake: Lake) -> None:
    build = build_membership(constituents("A", "B"), changes((D(2020, 1, 2), "B", "C")))
    tmp_lake.write_table("index_membership", build.intervals, mode="overwrite")
    universe = Universe(tmp_lake)
    assert universe.members(D(2019, 12, 31)) == ["A", "C"]
    assert universe.members(D(2020, 1, 2), name="sp500") == ["A", "B"]
    assert universe.members(AsOf.for_session(D(2020, 1, 2))) == ["A", "B"]
    # The cache follows lake writes made through the same Lake object.
    _membership(tmp_lake, ["Z"])
    assert universe.members(D(2020, 1, 2)) == ["Z"]


def test_unknown_universe_raises(tmp_lake: Lake) -> None:  # Review focus 5
    with pytest.raises(ValueError, match=r"unknown universe 'nasdaq'.*macro_etfs, sp500"):
        Universe(tmp_lake).members(AS_OF, "nasdaq")


def test_members_without_reference_data_raises(tmp_lake: Lake) -> None:  # Review focus 5
    with pytest.raises(DataError, match="refresh_universe_reference"):
        Universe(tmp_lake).members(AS_OF)


def test_members_rejects_datetime(tmp_lake: Lake) -> None:
    with pytest.raises(TypeError, match="AsOf"):
        Universe(tmp_lake).members(datetime(2024, 6, 3, 12, tzinfo=UTC), "macro_etfs")


def test_tradable_filters(market: Lake) -> None:
    assert Universe(market).tradable(AS_OF) == ["EDGE", "LIQ"]


def test_tradable_overrides(market: Lake) -> None:
    universe = Universe(market)
    assert universe.tradable(AS_OF, min_price=3.0) == ["CHEAP", "EDGE", "LIQ"]
    assert universe.tradable(AS_OF, min_adv_usd=5e6) == ["EDGE", "LIQ", "THIN"]
    assert universe.tradable(AS_OF, min_history_days=150) == ["EDGE", "LIQ", "YOUNG"]


def test_tradable_only_members(market: Lake) -> None:
    assert "OUTSIDER" not in Universe(market).tradable(AS_OF)


def test_member_without_prices_is_not_tradable(market: Lake) -> None:  # Review focus 4
    universe = Universe(market)
    assert "NOPRICE" in universe.members(AS_OF)
    assert "NOPRICE" not in universe.tradable(AS_OF)


def test_stale_member_is_not_tradable(market: Lake) -> None:  # Review focus 4
    tradable = Universe(market).tradable(AS_OF)
    assert "STALE" not in tradable  # 6 sessions old > max_stale_sessions 5
    assert "EDGE" in tradable  # exactly 5 sessions old


def test_tradable_ignores_bar_of_as_of_day(market: Lake) -> None:  # Review focus 3
    # THIN's bar of 2024-06-03 alone would lift its 20-day ADV far above 20e6, but at the
    # 08:00 decision time that bar (known 16:15 ET) does not exist yet.
    _fake_prices(market, {"THIN": [(AS_OF, 50.0, 1_000_000_000)]})
    assert "THIN" not in Universe(market).tradable(AS_OF)
    later = AsOf(day=AS_OF, cutoff=datetime(2024, 6, 3, 21, 0, tzinfo=UTC))  # 17:00 ET
    assert "THIN" in Universe(market).tradable(later)


def test_tradable_ignores_future_bars(market: Lake) -> None:
    before = Universe(market).tradable(AS_OF)
    future = CAL.sessions(D(2024, 6, 3), D(2024, 7, 31))
    _fake_prices(
        market,
        {
            "CHEAP": _bars(future, 80.0, 10_000_000),
            "THIN": _bars(future, 50.0, 100_000_000),
            "STALE": _bars(future, 50.0, 1_000_000),
            "NOPRICE": _bars(future, 50.0, 1_000_000),
        },
    )
    assert Universe(market).tradable(AS_OF) == before


def test_tradable_on_weekend_uses_friday_bars(tmp_lake: Lake) -> None:  # Review focus 3
    _membership(tmp_lake, ["FRI"])
    _fake_prices(tmp_lake, {"FRI": _bars(SESSIONS[-252:], 50.0, 1_000_000)})
    universe = Universe(tmp_lake)
    assert universe.tradable(D(2024, 6, 1)) == ["FRI"]  # Saturday: Friday's bar is known
    assert universe.tradable(D(2024, 5, 31)) == []  # Friday 08:00: only 251 bars known


def test_tradable_without_any_prices_is_empty(tmp_lake: Lake) -> None:  # Review focus 4
    assert not tmp_lake.has_table("prices_daily")
    assert Universe(tmp_lake).tradable(AS_OF, "macro_etfs") == []


def test_tradable_static_universe_uses_history(tmp_lake: Lake) -> None:
    _fake_prices(tmp_lake, {"SPY": _bars(SESSIONS, 500.0, 50_000_000)})
    assert Universe(tmp_lake).tradable(AS_OF, "macro_etfs") == ["SPY"]  # others have no bars


# --- Task 6: optional live refresh ---------------------------------------------------------------

REF = ReferenceDataConfig()


def test_fetch_sp500_pages_sends_user_agent() -> None:
    with respx.mock(assert_all_called=True) as router:
        a = router.get(REF.sp500_constituents_url).respond(200, text="<html>constituents</html>")
        b = router.get(REF.sp500_changes_url).respond(200, text="<html>changes</html>")
        pages = fetch_sp500_pages(REF)
    assert pages == ("<html>constituents</html>", "<html>changes</html>")
    for route in (a, b):
        assert route.calls.last.request.headers["User-Agent"] == REF.user_agent


def test_fetch_sp500_pages_http_error_raises() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(REF.sp500_constituents_url).respond(503)
        with pytest.raises(DataError, match="503"):
            fetch_sp500_pages(REF)


def test_fetch_sp500_pages_transport_error_raises() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(REF.sp500_constituents_url).mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(DataError, match="down") as info:
            fetch_sp500_pages(REF)
    assert isinstance(info.value.__cause__, httpx.ConnectError)


@pytest.mark.live
def test_live_wikipedia_pages_parse() -> None:
    constituents_page, changes_page = fetch_sp500_pages(REF)
    assert len(parse_constituents(constituents_page)) >= 490
    assert len(parse_changes(changes_page)) >= 400


# --- Task 7: acceptance criteria and documentation ---------------------------------------------


@pytest.fixture
def sp500_lake(tmp_lake: Lake, repo_ticker_map: TickerMap) -> Lake:
    refresh_universe_reference(
        tmp_lake, constituents_html(), changes_html(), ticker_map=repo_ticker_map
    )
    return tmp_lake


def test_acceptance_members_2019_06_03(sp500_lake: Lake) -> None:
    """Brief: `members(date(2019,6,3))` excludes TSLA and includes names later removed."""
    members = Universe(sp500_lake).members(date(2019, 6, 3))
    today = set(parse_constituents(constituents_html())["symbol"])
    assert "TSLA" not in members
    later_removed = set(members) - today
    assert {"AIV", "XEC", "CELG", "ETFC", "CTXS", "ATVI"} <= later_removed
    assert len(later_removed) >= 90
    assert 500 <= len(members) <= 510
    assert "META" in members  # Facebook, under its current ticker
    assert "TSLA" in Universe(sp500_lake).members(date(2020, 12, 21))


def test_acceptance_tradable_uses_only_data_before_as_of(sp500_lake: Lake, tmp_path: Path) -> None:
    """Brief: `tradable` uses only data before `as_of`."""
    as_of = AsOf.for_session(D(2019, 6, 3))
    history = CAL.sessions(CAL.offset(D(2019, 5, 31), -299), D(2019, 5, 31))
    future = CAL.sessions(D(2019, 6, 3), D(2019, 7, 31))
    known = {
        "AAPL": _bars(history, 175.0, 30_000_000),
        "AIV": _bars(history, 45.0, 1_000_000),  # member then, removed 2020-12-21
        "TSLA": _bars(history, 40.0, 30_000_000),  # not a member until 2020-12-21
        "XEC": _bars(history, 60.0, 100_000),  # ADV 6e6, too thin
    }
    _fake_prices(sp500_lake, known)
    assert Universe(sp500_lake).tradable(as_of) == ["AAPL", "AIV"]

    # Bars on or after as_of (here: the thin name turning liquid, prices collapsing) must
    # change nothing, and the result must equal the one from a lake holding only known bars.
    _fake_prices(
        sp500_lake,
        {
            "AAPL": _bars(future, 1.0, 1),
            "AIV": _bars(future, 1.0, 1),
            "XEC": _bars(future, 60.0, 500_000_000),
        },
    )
    assert Universe(sp500_lake).tradable(as_of) == ["AAPL", "AIV"]
    stored = sp500_lake.read_table("prices_daily")
    assert (stored["date"] >= as_of.day).any()
    truncated = Lake(tmp_path / "truncated", registry=sp500_lake.registry.copy())
    for table in ("index_membership", "instruments"):
        truncated.write_table(table, sp500_lake.read_table(table), mode="overwrite")
    truncated.write_table("prices_daily", pit_filter(stored, as_of), mode="overwrite")
    assert Universe(truncated).tradable(as_of) == Universe(sp500_lake).tradable(as_of)


def test_data_limitations_doc(repo_root: Path) -> None:
    text = (repo_root / "docs" / "data-limitations.md").read_text(encoding="utf-8").lower()
    for word in ("survivorship", "delisted", "ticker", "gics", "anomal", "announce"):
        assert word in text, word
