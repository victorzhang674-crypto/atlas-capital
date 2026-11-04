"""Stage 07 Task 2: the yfinance adapter, on recorded fixtures (no network)."""

import io
import json
import math
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from price_helpers import FakeYFinance, load_yf_symbol

from atlas.core.config import PriceDataConfig
from atlas.core.errors import ConfigError, DataError
from atlas.core.logging import configure_logging, reset_logging
from atlas.data.providers.base import ACTION_COLUMNS, DAILY_COLUMNS, PriceProvider
from atlas.data.providers.yfinance_provider import YFinanceProvider, load_yahoo_no_data

START, END = date(2020, 8, 3), date(2020, 9, 30)


def _provider(
    fake: FakeYFinance, sleeps: list[float] | None = None, **cfg: object
) -> YFinanceProvider:
    sink = sleeps if sleeps is not None else []
    return YFinanceProvider(
        PriceDataConfig(**cfg),  # type: ignore[arg-type]
        download=fake.download,
        splits=fake.splits,
        sleep=sink.append,
    )


def _row(frame: pd.DataFrame, symbol: str, day: date) -> pd.Series:
    rows = frame[(frame["symbol"] == symbol) & (frame["date"] == day)]
    assert len(rows) == 1, f"expected one {symbol} row on {day}"
    return rows.iloc[0]


def test_is_a_price_provider() -> None:
    assert isinstance(_provider(FakeYFinance()), PriceProvider)
    assert _provider(FakeYFinance()).name == "yfinance"


def test_aapl_prices_are_unadjusted_before_split() -> None:
    out = _provider(FakeYFinance()).fetch_daily(["AAPL"], START, END)
    assert tuple(out.columns) == DAILY_COLUMNS
    assert len(out) == 42
    before = _row(out, "AAPL", date(2020, 8, 28))
    assert before["close"] == pytest.approx(124.807503 * 4, abs=1e-4)  # 499.23 as traded
    assert before["open"] == pytest.approx(126.012497 * 4, abs=1e-4)
    assert before["volume"] == 187630000 // 4
    on_split = _row(out, "AAPL", date(2020, 8, 31))
    assert on_split["close"] == pytest.approx(129.039993, abs=1e-5)
    assert on_split["volume"] == 225702700
    assert set(out["vendor"]) == {"yfinance"}


def test_split_after_window_still_unadjusts() -> None:  # Review focus 3
    out = _provider(FakeYFinance()).fetch_daily(["AAPL"], START, date(2020, 8, 28))
    assert out["date"].max() == date(2020, 8, 28)
    assert _row(out, "AAPL", date(2020, 8, 28))["close"] == pytest.approx(499.23, abs=1e-3)


def test_adj_close_is_vendor_value() -> None:
    out = _provider(FakeYFinance()).fetch_daily(["AAPL"], START, END)
    vendor = load_yf_symbol("AAPL").loc["2020-08-28", "Adj Close"]
    assert _row(out, "AAPL", date(2020, 8, 28))["adj_close"] == pytest.approx(vendor)


def test_actions_aapl_split_and_raw_dividend() -> None:
    out = _provider(FakeYFinance()).fetch_actions(["AAPL"], START, END)
    assert tuple(out.columns) == ACTION_COLUMNS
    assert list(zip(out["ex_date"], out["type"], strict=True)) == [
        (date(2020, 8, 7), "dividend"),
        (date(2020, 8, 31), "split"),
    ]
    div, split = out.iloc[0], out.iloc[1]
    assert div["amount"] == pytest.approx(0.82) and math.isnan(div["ratio"])
    assert split["ratio"] == 4.0 and math.isnan(split["amount"])


def test_actions_window_excludes_splits_outside_it() -> None:
    out = _provider(FakeYFinance()).fetch_actions(["AAPL"], date(2020, 9, 1), END)
    assert out.empty


def test_actions_spy_dividend() -> None:
    out = _provider(FakeYFinance()).fetch_actions(["SPY"], START, END)
    assert list(out["ex_date"]) == [date(2020, 9, 18)]
    assert out["amount"].iloc[0] == pytest.approx(1.339)


def test_delisted_symbol_is_absent_not_an_error() -> None:
    fake = FakeYFinance()
    provider = _provider(fake)
    daily = provider.fetch_daily(["AAPL", "SIVB", "SPY"], START, END)
    assert sorted(set(daily["symbol"])) == ["AAPL", "SPY"]
    actions = provider.fetch_actions(["SIVB"], START, END)
    assert actions.empty
    assert "SIVB" not in fake.split_calls  # no rows → no split lookup needed


def test_download_args_end_exclusive_and_vendor_symbols() -> None:
    brk = load_yf_symbol("SPY")  # any recorded frame stands in for BRK-B
    fake = FakeYFinance(frames={"BRK-B": brk}, splits={})
    out = _provider(fake).fetch_daily(["brk.b"], START, END)
    assert fake.download_calls == [(["BRK-B"], START, date(2020, 10, 1))]
    assert set(out["symbol"]) == {"BRK.B"}


def test_batches_and_throttles() -> None:
    fake = FakeYFinance()
    sleeps: list[float] = []
    provider = _provider(fake, sleeps, batch_size=2, min_request_interval_seconds=0.5)
    out = provider.fetch_daily(["AAPL", "SPY", "SIVB"], START, END)
    assert [call[0] for call in fake.download_calls] == [["AAPL", "SPY"], ["SIVB"]]
    # 2 downloads + 2 split-history lookups (AAPL, SPY) = 4 requests → 3 pauses
    assert fake.split_calls == ["AAPL", "SPY"]
    assert sleeps == [0.5, 0.5, 0.5]
    assert len(out) == 84


def test_retry_then_success() -> None:
    fake = FakeYFinance(fail_times=2)
    sleeps: list[float] = []
    out = _provider(fake, sleeps, backoff_seconds=1.0, min_request_interval_seconds=0).fetch_daily(
        ["AAPL"], START, END
    )
    assert len(out) == 42
    assert sleeps == [1.0, 2.0]


def test_download_keeps_failing_raises_data_error() -> None:
    fake = FakeYFinance(fail_times=10)
    with pytest.raises(DataError, match="yfinance download"):
        _provider(fake, max_retries=2).fetch_daily(["AAPL"], START, END)
    assert len(fake.download_calls) == 3


def test_any_yfinance_error_becomes_chained_data_error() -> None:  # audit item 4
    calls: list[str] = []

    def broken(symbols: list[str], start: date, end_exclusive: date) -> pd.DataFrame:
        calls.append("download")
        raise TypeError("download() got an unexpected keyword argument 'actions'")

    provider = YFinanceProvider(
        PriceDataConfig(max_retries=1),
        download=broken,
        splits=FakeYFinance().splits,
        sleep=lambda s: None,
    )
    with pytest.raises(DataError, match="yfinance download") as info:
        provider.fetch_daily(["AAPL"], START, END)
    assert isinstance(info.value.__cause__, TypeError)  # the original error stays visible
    assert calls == ["download", "download"]  # retried like a vendor failure


def test_symbol_without_split_history_is_dropped_and_logged(tmp_path: Path) -> None:
    # Plan D2: a traded symbol whose split history is `None` is left out (logged), so the
    # rest of its batch is still served (audit item 1; this replaced a test that expected a raise).
    fake = FakeYFinance(no_splits=("AAPL",))
    provider = _provider(fake)
    log_file = tmp_path / "atlas.log"
    configure_logging(log_file=log_file, console=io.StringIO())
    try:
        daily = provider.fetch_daily(["AAPL", "SPY"], START, END)
        actions = provider.fetch_actions(["AAPL", "SPY"], START, END)
    finally:
        reset_logging()
    assert set(daily["symbol"]) == {"SPY"} and len(daily) == 42
    assert set(actions["symbol"]) == {"SPY"}
    lines = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    warned = [ln for ln in lines if ln["event"] == "prices.no_split_history"]
    assert [(ln["level"], ln["symbol"]) for ln in warned] == [("warning", "AAPL")]


def test_actions_reuse_the_daily_download() -> None:
    fake = FakeYFinance()
    provider = _provider(fake)
    provider.fetch_daily(["AAPL", "SPY"], START, END)
    provider.fetch_actions(["AAPL", "SPY"], START, END)
    provider.fetch_daily(["AAPL"], START, END)
    assert len(fake.download_calls) == 1
    assert fake.split_calls == ["AAPL", "SPY"]


def test_download_memo_is_bounded() -> None:
    # Phase 1 checkpoint, Tier 3: the memo kept every downloaded window for the provider's life.
    frames = {s: load_yf_symbol(s) for s in ("AAPL", "SPY")}
    frames["QQQ"] = frames["SPY"]
    fake = FakeYFinance(frames=frames, splits={})
    provider = _provider(fake, memo_size=2)
    out = provider.fetch_daily(["AAPL", "SPY", "QQQ"], START, END)  # more than the bound
    assert sorted(set(out["symbol"])) == ["AAPL", "QQQ", "SPY"]
    assert len(fake.download_calls) == 1
    provider.fetch_daily(["SPY", "QQQ"], START, END)  # the two most recent: still memoised
    assert len(fake.download_calls) == 1
    provider.fetch_daily(["AAPL"], START, END)  # evicted: fetched again
    assert [call[0] for call in fake.download_calls[1:]] == [["AAPL"]]


def test_known_no_data_symbols_are_not_requested() -> None:
    # Phase 1 checkpoint, Tier 4: Yahoo spends ~25 s on each name it has no data for; names
    # recorded as such (config/yahoo_no_data.yaml) are not asked again, as if Yahoo had
    # returned nothing, so a fallback provider serves them (Alpaca when its keys are set).
    fake = FakeYFinance()
    provider = YFinanceProvider(
        PriceDataConfig(), download=fake.download, splits=fake.splits, sleep=lambda s: None,
        no_data=("spy", "SIVB"),
    )  # fmt: skip
    assert provider.no_data == frozenset({"SPY", "SIVB"})
    daily = provider.fetch_daily(["AAPL", "SPY", "SIVB"], START, END)
    actions = provider.fetch_actions(["AAPL", "SPY"], START, END)
    assert set(daily["symbol"]) == {"AAPL"} and set(actions["symbol"]) == {"AAPL"}
    assert [call[0] for call in fake.download_calls] == [["AAPL"]]
    assert fake.split_calls == ["AAPL"]


def test_yahoo_no_data_file(repo_root: Path, tmp_path: Path) -> None:
    known = load_yahoo_no_data(repo_root / "config" / "yahoo_no_data.yaml")
    assert {"YHOO", "WFM", "AVB", "ACE"} <= known and len(known) == 64
    assert load_yahoo_no_data(tmp_path / "none.yaml") == frozenset()
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "symbols: [{symbol: AAA, checked: 2026-11-01}, {symbol: aaa, checked: 2026-11-02}]\n"
    )
    with pytest.raises(ConfigError, match=r"bad\.yaml"):
        load_yahoo_no_data(bad)


def test_tz_aware_index_uses_new_york_date() -> None:
    frame = load_yf_symbol("SPY")
    frame.index = frame.index.tz_localize("America/New_York")
    fake = FakeYFinance(frames={"SPY": frame}, splits={})
    out = _provider(fake).fetch_daily(["SPY"], START, END)
    assert out["date"].iloc[0] == START
    assert len(out) == 42


def test_partial_nan_row_is_kept_for_validation() -> None:  # Review focus 2
    frame = load_yf_symbol("SPY")
    frame.loc["2020-08-04", "Volume"] = float("nan")
    fake = FakeYFinance(frames={"SPY": frame}, splits={})
    out = _provider(fake).fetch_daily(["SPY"], START, END)
    assert len(out) == 42
    assert pd.isna(_row(out, "SPY", date(2020, 8, 4))["volume"])


def test_dividend_on_a_day_without_a_bar_is_kept() -> None:
    frame = load_yf_symbol("SPY")
    frame.loc["2020-09-18", ["Open", "High", "Low", "Close", "Adj Close", "Volume"]] = float("nan")
    fake = FakeYFinance(frames={"SPY": frame}, splits={})
    provider = _provider(fake)
    daily = provider.fetch_daily(["SPY"], START, END)
    assert len(daily) == 41 and date(2020, 9, 18) not in set(daily["date"])
    actions = provider.fetch_actions(["SPY"], START, END)
    assert list(actions["ex_date"]) == [date(2020, 9, 18)]
    assert actions["amount"].iloc[0] == pytest.approx(1.339)


def test_empty_request_makes_no_call() -> None:
    fake = FakeYFinance()
    assert _provider(fake).fetch_daily([], START, END).empty
    assert fake.download_calls == []
