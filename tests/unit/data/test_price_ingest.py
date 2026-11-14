"""Stage 07 Task 6: ingesting prices and corporate actions into the lake."""

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest
from price_helpers import FakePriceProvider, FakeYFinance, daily_rows, load_yf_symbol

from atlas.core.calendar import TradingCalendar
from atlas.core.config import PriceDataConfig
from atlas.core.errors import ConfigError
from atlas.core.pit import AsOf, pit_filter
from atlas.core.storage import Lake
from atlas.data.prices import (
    CORPORATE_ACTIONS,
    PRICES_DAILY,
    IngestReport,
    ingest_prices,
    load_reused_tickers,
    purge_reused_tickers,
)
from atlas.data.providers.yfinance_provider import YFinanceProvider

START, END = date(2020, 8, 3), date(2020, 9, 30)


def _yahoo(fake: FakeYFinance | None = None) -> YFinanceProvider:
    fake = fake if fake is not None else FakeYFinance()
    return YFinanceProvider(download=fake.download, splits=fake.splits, sleep=lambda s: None)


def _ingest(lake: Lake, provider: object, symbols: list[str], **kw: object) -> IngestReport:
    return ingest_prices(lake, provider, symbols, START, END, **kw)  # type: ignore[arg-type]


def test_tables_are_registered(tmp_lake: Lake) -> None:
    assert tmp_lake.table_schema("prices_daily") == PRICES_DAILY
    assert tmp_lake.table_schema("corporate_actions") == CORPORATE_ACTIONS
    assert PRICES_DAILY.primary_key == ("symbol", "date")
    assert CORPORATE_ACTIONS.primary_key == ("symbol", "ex_date", "type")


def test_ingest_fixtures_writes_prices_and_actions(tmp_lake: Lake) -> None:
    report = _ingest(tmp_lake, _yahoo(), ["AAPL", "SPY", "SIVB"])
    assert report.symbols_ok == ("AAPL", "SPY")
    assert report.symbols_failed == ("SIVB",)
    assert "no price data" in report.failure_reasons["SIVB"]
    assert report.rows == 84 and report.action_rows == 3
    prices = tmp_lake.read_table("prices_daily")
    assert prices.groupby("symbol").size().to_dict() == {"AAPL": 42, "SPY": 42}
    aapl = prices[prices["symbol"] == "AAPL"].set_index("date")
    assert aapl.loc[date(2020, 8, 28), "close"] == pytest.approx(499.23, abs=1e-3)
    actions = tmp_lake.read_table("corporate_actions")
    assert list(zip(actions["symbol"], actions["type"], strict=True)) == [
        ("AAPL", "dividend"),
        ("AAPL", "split"),
        ("SPY", "dividend"),
    ]


def test_available_at_is_close_plus_lag(tmp_lake: Lake) -> None:
    _ingest(tmp_lake, _yahoo(), ["AAPL"])
    prices = tmp_lake.read_table("prices_daily").set_index("date")
    # 16:00 EDT close + 15 min = 20:15 UTC
    assert prices.loc[date(2020, 8, 31), "available_at"] == pd.Timestamp("2020-08-31 20:15Z")


def test_available_at_on_early_close(tmp_lake: Lake) -> None:
    day = date(2020, 11, 27)  # day after Thanksgiving: 13:00 EST close
    provider = FakePriceProvider(daily_rows("SPY", [(day, 363.67, 1000)]))
    ingest_prices(tmp_lake, provider, ["SPY"], day, day)
    stamp = tmp_lake.read_table("prices_daily")["available_at"].iloc[0]
    assert stamp == pd.Timestamp("2020-11-27 18:15Z")


def test_action_available_at_is_ex_date_bar(tmp_lake: Lake) -> None:
    _ingest(tmp_lake, _yahoo(), ["AAPL"])
    actions = tmp_lake.read_table("corporate_actions").set_index("type")
    assert actions.loc["split", "available_at"] == pd.Timestamp("2020-08-31 20:15Z")


def test_available_at_uses_calendar_config(tmp_lake: Lake) -> None:
    from atlas.core.config import CalendarConfig

    cal = TradingCalendar(config=CalendarConfig(bar_publish_lag_minutes=30))
    _ingest(tmp_lake, _yahoo(), ["AAPL"], calendar=cal)
    prices = tmp_lake.read_table("prices_daily").set_index("date")
    assert prices.loc[date(2020, 8, 31), "available_at"] == pd.Timestamp("2020-08-31 20:30Z")


def test_ingested_prices_respect_point_in_time(tmp_lake: Lake) -> None:
    _ingest(tmp_lake, _yahoo(), ["AAPL"])
    prices = tmp_lake.read_table("prices_daily")
    known = pit_filter(prices, AsOf.for_session(date(2020, 8, 31)))
    assert known["date"].max() == date(2020, 8, 28)  # the split-day bar is not yet known


def test_reingest_is_idempotent(tmp_lake: Lake) -> None:
    first = _ingest(tmp_lake, _yahoo(), ["AAPL", "SPY", "SIVB"])
    prices_1 = tmp_lake.read_table("prices_daily")
    actions_1 = tmp_lake.read_table("corporate_actions")
    second = _ingest(tmp_lake, _yahoo(), ["AAPL", "SPY", "SIVB"])
    pd.testing.assert_frame_equal(tmp_lake.read_table("prices_daily"), prices_1)
    pd.testing.assert_frame_equal(tmp_lake.read_table("corporate_actions"), actions_1)
    assert (second.rows, second.symbols_ok, second.symbols_failed) == (
        first.rows,
        first.symbols_ok,
        first.symbols_failed,
    )
    assert len(tmp_lake.table_files("prices_daily")) == 1  # one year partition


def test_reingest_replaces_changed_values(tmp_lake: Lake) -> None:
    day = date(2020, 8, 3)
    ingest_prices(
        tmp_lake, FakePriceProvider(daily_rows("SPY", [(day, 1.0, 10)])), ["SPY"], day, day
    )
    ingest_prices(
        tmp_lake, FakePriceProvider(daily_rows("SPY", [(day, 2.0, 20)])), ["SPY"], day, day
    )
    prices = tmp_lake.read_table("prices_daily")
    assert list(prices["close"]) == [2.0] and list(prices["volume"]) == [20]


def test_failed_symbol_never_aborts_batch(tmp_lake: Lake) -> None:
    day = date(2020, 8, 3)
    daily = pd.concat([daily_rows(s, [(day, 10.0, 100)]) for s in ("AAPL", "BAD", "SPY")])
    provider = FakePriceProvider(daily, fail_symbols=["BAD"])
    report = ingest_prices(tmp_lake, provider, ["AAPL", "BAD", "SPY"], day, day)
    assert report.symbols_ok == ("AAPL", "SPY")
    assert report.symbols_failed == ("BAD",)
    assert "simulated vendor failure" in report.failure_reasons["BAD"]
    assert set(tmp_lake.read_table("prices_daily")["symbol"]) == {"AAPL", "SPY"}


def test_batch_error_retries_symbol_by_symbol(tmp_lake: Lake) -> None:
    day = date(2020, 8, 3)
    daily = pd.concat([daily_rows(s, [(day, 10.0, 100)]) for s in ("AAPL", "BAD")])
    provider = FakePriceProvider(daily, fail_symbols=["BAD"])
    ingest_prices(tmp_lake, provider, ["AAPL", "BAD"], day, day)
    daily_calls = [call[1] for call in provider.calls if call[0] == "daily"]
    assert daily_calls == [("AAPL", "BAD"), ("AAPL",), ("BAD",)]


def test_incomplete_bar_is_dropped_not_the_symbol(tmp_lake: Lake) -> None:
    # Phase 1 checkpoint: one vendor bar with a missing field cost AVB its whole history.
    spy = load_yf_symbol("SPY")
    spy.loc["2020-08-04", "Volume"] = float("nan")
    spy.loc["2020-08-05", "Open"] = float("nan")
    fake = FakeYFinance(frames={"AAPL": load_yf_symbol("AAPL"), "SPY": spy})
    report = _ingest(tmp_lake, _yahoo(fake), ["AAPL", "SPY"])
    assert report.symbols_ok == ("AAPL", "SPY")
    assert report.symbols_failed == ()
    assert report.bars_dropped == {"SPY": (date(2020, 8, 4), date(2020, 8, 5))}
    prices = tmp_lake.read_table("prices_daily")
    spy_days = set(prices.loc[prices["symbol"] == "SPY", "date"])
    assert len(spy_days) == 40
    assert not {date(2020, 8, 4), date(2020, 8, 5)} & spy_days
    assert set(tmp_lake.read_table("corporate_actions")["symbol"]) == {"AAPL", "SPY"}


def test_complete_bars_report_nothing_dropped(tmp_lake: Lake) -> None:
    report = _ingest(tmp_lake, _yahoo(), ["AAPL", "SPY"])
    assert report.bars_dropped == {}


def test_symbol_with_only_incomplete_bars_fails_alone(tmp_lake: Lake) -> None:
    spy = load_yf_symbol("SPY")
    spy["Close"] = float("nan")
    fake = FakeYFinance(frames={"AAPL": load_yf_symbol("AAPL"), "SPY": spy})
    report = _ingest(tmp_lake, _yahoo(fake), ["AAPL", "SPY"])
    assert report.symbols_ok == ("AAPL",)
    assert report.symbols_failed == ("SPY",)
    assert "no complete price bars" in report.failure_reasons["SPY"]
    assert "SPY" not in report.bars_dropped
    assert set(tmp_lake.read_table("prices_daily")["symbol"]) == {"AAPL"}
    assert set(tmp_lake.read_table("corporate_actions")["symbol"]) == {"AAPL"}


def test_bar_on_non_session_fails_symbol(tmp_lake: Lake) -> None:  # Review focus 1
    labor_day = date(2020, 9, 7)
    daily = pd.concat(
        [
            daily_rows("AAPL", [(date(2020, 9, 4), 120.0, 100), (labor_day, 121.0, 100)]),
            daily_rows("SPY", [(date(2020, 9, 4), 342.0, 100)]),
        ]
    )
    report = ingest_prices(
        tmp_lake, FakePriceProvider(daily), ["AAPL", "SPY"], date(2020, 9, 4), labor_day
    )
    assert report.symbols_failed == ("AAPL",)
    assert "2020-09-07" in report.failure_reasons["AAPL"]
    assert report.symbols_ok == ("SPY",)


def test_symbol_without_split_history_fails_alone(tmp_lake: Lake) -> None:  # audit item 1
    report = _ingest(tmp_lake, _yahoo(FakeYFinance(no_splits=("AAPL",))), ["AAPL", "SPY"])
    assert report.symbols_ok == ("SPY",)
    assert report.symbols_failed == ("AAPL",)
    assert "no price data" in report.failure_reasons["AAPL"]


def test_actions_error_fails_symbol(tmp_lake: Lake) -> None:
    day = date(2020, 8, 3)
    daily = pd.concat([daily_rows(s, [(day, 10.0, 100)]) for s in ("AAPL", "SPY")])
    provider = FakePriceProvider(daily, fail_actions=["AAPL"])
    report = ingest_prices(tmp_lake, provider, ["AAPL", "SPY"], day, day)
    assert report.symbols_failed == ("AAPL",)
    assert "actions" in report.failure_reasons["AAPL"]
    assert set(tmp_lake.read_table("prices_daily")["symbol"]) == {"SPY"}


def test_empty_request_writes_nothing(tmp_lake: Lake) -> None:
    report = ingest_prices(tmp_lake, FakePriceProvider(), [], START, END)
    assert report == IngestReport(
        rows=0,
        action_rows=0,
        symbols_ok=(),
        symbols_failed=(),
        failure_reasons=report.failure_reasons,
        mismatches=(),
    )
    assert dict(report.failure_reasons) == {}
    assert not tmp_lake.has_table("prices_daily")


def test_all_symbols_failing_writes_nothing(tmp_lake: Lake) -> None:
    report = _ingest(tmp_lake, _yahoo(), ["SIVB"])
    assert report.symbols_failed == ("SIVB",) and report.rows == 0
    assert not tmp_lake.has_table("prices_daily")


def test_available_at_never_before_the_session_close(tmp_lake: Lake) -> None:
    _ingest(tmp_lake, _yahoo(), ["SPY"])
    prices = tmp_lake.read_table("prices_daily")
    cal = TradingCalendar()
    for day, stamp in zip(prices["date"], prices["available_at"], strict=True):
        assert stamp.to_pydatetime() > cal.session_close(day)
        assert (
            stamp.tzinfo is not None
            and stamp.utcoffset() == datetime(2020, 1, 1, tzinfo=UTC).utcoffset()
        )


# --- Phase 2 checkpoint (backlog, Stages 07/08): reused tickers ------------------------------


def _reused_provider() -> FakePriceProvider:
    """SE before and after 2020-08-31: a member until then, another company's listing later."""
    days = [d for d in TradingCalendar().sessions(START, END)]
    daily = pd.concat(
        [
            daily_rows("SE", [(d, 50.0 + i, 1000) for i, d in enumerate(days)]),
            daily_rows("AAPL", [(d, 100.0, 1000) for d in days]),
        ],
        ignore_index=True,
    )
    actions = pd.DataFrame(
        {
            "symbol": ["SE", "SE"],
            "ex_date": [date(2020, 8, 14), date(2020, 9, 15)],
            "type": ["dividend", "dividend"],
            "ratio": [None, None],
            "amount": [0.5, 0.7],
            "vendor": ["fake", "fake"],
        }
    )
    return FakePriceProvider(daily, actions)


def test_reused_ticker_bars_after_the_member_are_not_stored(tmp_lake: Lake) -> None:
    until = date(2020, 8, 31)
    report = _ingest(tmp_lake, _reused_provider(), ["SE", "AAPL"], reused={"SE": until})
    assert report.symbols_ok == ("SE", "AAPL")
    prices = tmp_lake.read_table("prices_daily")
    se = prices[prices["symbol"] == "SE"]
    assert se["date"].max() == until and len(se) == 21
    assert (prices["symbol"] == "AAPL").sum() == 42  # other symbols untouched
    actions = tmp_lake.read_table("corporate_actions")
    assert list(actions["ex_date"]) == [date(2020, 8, 14)]


def test_a_reused_ticker_with_only_later_bars_has_no_data(tmp_lake: Lake) -> None:
    report = _ingest(tmp_lake, _reused_provider(), ["SE"], reused={"SE": date(2017, 2, 27)})
    assert report.symbols_failed == ("SE",)
    assert report.failure_reasons["SE"] == "no price data returned"
    assert not tmp_lake.has_table("prices_daily") or tmp_lake.read_table("prices_daily").empty


def test_purge_reused_tickers(tmp_lake: Lake) -> None:
    _ingest(tmp_lake, _reused_provider(), ["SE", "AAPL"])
    removed = purge_reused_tickers(tmp_lake, {"SE": date(2020, 8, 31), "ZZZ": date(2020, 1, 2)})
    assert removed == {"SE": (21, 1), "ZZZ": (0, 0)}
    prices = tmp_lake.read_table("prices_daily")
    assert prices.loc[prices["symbol"] == "SE", "date"].max() == date(2020, 8, 31)
    assert (prices["symbol"] == "AAPL").sum() == 42
    assert purge_reused_tickers(tmp_lake, {"SE": date(2020, 8, 31)}) == {"SE": (0, 0)}


def test_load_reused_tickers(tmp_path: Path, repo_root: Path) -> None:
    path = tmp_path / "reused.yaml"
    path.write_text(
        "tickers:\n"
        "  - {symbol: se, until: 2017-02-27, note: Spectra Energy acquired by Enbridge}\n"
        "  - {symbol: BRK-B, until: 2020-01-02, note: x}\n"
    )
    assert load_reused_tickers(path) == {"SE": date(2017, 2, 27), "BRK.B": date(2020, 1, 2)}
    assert load_reused_tickers(tmp_path / "none.yaml") == {}
    path.write_text(
        "tickers:\n  - {symbol: SE, until: 2017-02-27}\n  - {symbol: se, until: 2018-01-02}\n"
    )
    with pytest.raises(ConfigError, match="twice"):
        load_reused_tickers(path)
    path.write_text("tickers:\n  - {symbol: SE}\n")
    with pytest.raises(ConfigError, match="until"):
        load_reused_tickers(path)
    repo = load_reused_tickers(repo_root / "config" / "reused_tickers.yaml")
    assert len(repo) == 26
    assert repo["SE"] == date(2017, 2, 27) and repo["STI"] == date(2019, 12, 6)
    for genuine in ("JWN", "FL", "TGNA", "PDCO", "SWN", "AVB", "AA"):
        assert genuine not in repo, genuine
    assert PriceDataConfig().reused_tickers_file == Path("config/reused_tickers.yaml")
