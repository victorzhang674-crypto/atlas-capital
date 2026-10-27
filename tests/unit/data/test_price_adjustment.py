"""Stage 07 Task 7: our own adjustment factors, cross-checked against the vendor's adj_close."""

from datetime import date

import numpy as np
import pandas as pd
import pytest
from price_helpers import FakePriceProvider, FakeYFinance, daily_rows

from atlas.core.errors import DataError
from atlas.core.storage import Lake
from atlas.data.prices import adjustment_factors, cross_check_adjustment, ingest_prices
from atlas.data.providers.yfinance_provider import YFinanceProvider

START, END = date(2020, 8, 3), date(2020, 9, 30)
SPLIT_DAY = date(2020, 8, 31)


def _fixture(symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    fake = FakeYFinance()
    provider = YFinanceProvider(download=fake.download, splits=fake.splits, sleep=lambda s: None)
    return provider.fetch_daily([symbol], START, END), provider.fetch_actions([symbol], START, END)


def _at(frame: pd.DataFrame, day: date, col: str) -> float:
    return float(frame.loc[frame["date"] == day, col].iloc[0])


def _actions(*rows: tuple[str, date, str, float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "ex_date": [r[1] for r in rows],
            "type": [r[2] for r in rows],
            "ratio": [r[3] if r[2] == "split" else float("nan") for r in rows],
            "amount": [r[3] if r[2] == "dividend" else float("nan") for r in rows],
            "vendor": ["fake"] * len(rows),
        }
    )


def test_aapl_split_adjusted_series_is_continuous() -> None:
    daily, actions = _fixture("AAPL")
    adj = adjustment_factors(daily, actions)
    assert list(adj["date"]) == list(daily["date"])  # same row order
    before = _at(adj, date(2020, 8, 28), "split_adj_close")
    on_split = _at(adj, SPLIT_DAY, "split_adj_close")
    assert before == pytest.approx(124.807503, abs=1e-4)
    assert on_split == pytest.approx(129.039993, abs=1e-5)
    assert on_split / before - 1 == pytest.approx(0.0339, abs=1e-4)  # +3.39%, not -74%
    raw_move = _at(daily, SPLIT_DAY, "close") / _at(daily, date(2020, 8, 28), "close") - 1
    assert raw_move < -0.70
    assert set(adj.loc[adj["date"] < SPLIT_DAY, "split_factor"]) == {0.25}
    assert set(adj.loc[adj["date"] >= SPLIT_DAY, "split_factor"]) == {1.0}


def test_aapl_recomputed_matches_vendor_within_tolerance() -> None:
    daily, actions = _fixture("AAPL")
    assert cross_check_adjustment(daily, actions, 0.005) == ()
    adj = adjustment_factors(daily, actions)
    ours = adj["calc_adj_close"] / adj["calc_adj_close"].iloc[-1]
    vendor = adj["adj_close"] / adj["adj_close"].iloc[-1]
    assert float((ours / vendor - 1).abs().max()) < 0.0005


def test_spy_dividend_factor() -> None:
    daily, actions = _fixture("SPY")
    adj = adjustment_factors(daily, actions)
    expected = 1 - 1.339 / _at(daily, date(2020, 9, 17), "close")
    before = adj.loc[adj["date"] < date(2020, 9, 18), "total_factor"]
    after = adj.loc[adj["date"] >= date(2020, 9, 18), "total_factor"]
    assert np.allclose(before, expected) and np.allclose(after, 1.0)
    assert cross_check_adjustment(daily, actions, 0.005) == ()


def test_tampered_vendor_adj_close_is_recorded() -> None:
    daily, actions = _fixture("AAPL")
    tampered = daily.copy()
    pre = tampered["date"] < SPLIT_DAY
    tampered.loc[pre, "adj_close"] *= 4  # vendor "forgot" the split
    mismatches = cross_check_adjustment(tampered, actions, 0.005)
    assert len(mismatches) == int(pre.sum()) == 20
    first = mismatches[0]
    assert (first.symbol, first.date) == ("AAPL", START)
    assert first.rel_diff == pytest.approx(-0.75, abs=1e-3)


def test_reverse_split_and_first_bar_dividend() -> None:  # Review focus 4
    d1, d2, d3, d4 = date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6), date(2024, 3, 7)
    daily = daily_rows(
        "XYZ", [(d1, 10.0, 1000), (d2, 10.0, 1000), (d3, 100.0, 100), (d4, 100.0, 100)]
    )
    actions = _actions(
        ("XYZ", d1, "dividend", 1.0),  # first bar: no earlier close, affects no row
        ("XYZ", d3, "split", 0.1),  # 1-for-10 reverse split
        ("XYZ", d4, "dividend", 2.0),
    )
    adj = adjustment_factors(daily, actions)
    assert list(adj["split_adj_close"]) == pytest.approx([100.0, 100.0, 100.0, 100.0])
    assert list(adj["total_factor"]) == pytest.approx([9.8, 9.8, 0.98, 1.0])


def test_split_and_dividend_on_the_same_ex_date() -> None:
    d1, d2, d3 = date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6)
    daily = daily_rows("XYZ", [(d1, 100.0, 10), (d2, 100.0, 10), (d3, 25.0, 40)])
    actions = _actions(("XYZ", d3, "split", 4.0), ("XYZ", d3, "dividend", 0.25))
    adj = adjustment_factors(daily, actions)
    # the ex-date dividend is per new share: compare with the prior close in new-share terms (25)
    assert list(adj["total_factor"]) == pytest.approx([0.25 * 0.99, 0.25 * 0.99, 1.0])


def test_dividend_not_below_price_raises() -> None:
    d1, d2 = date(2024, 3, 4), date(2024, 3, 5)
    daily = daily_rows("XYZ", [(d1, 1.0, 10), (d2, 1.0, 10)])
    with pytest.raises(DataError, match="dividend"):
        adjustment_factors(daily, _actions(("XYZ", d2, "dividend", 1.5)))


def test_actions_of_other_symbols_are_ignored() -> None:
    daily, _ = _fixture("SPY")
    adj = adjustment_factors(daily, _actions(("QQQ", date(2020, 9, 1), "split", 2.0)))
    assert set(adj["total_factor"]) == {1.0}


def test_ingest_report_carries_mismatches(tmp_lake: Lake) -> None:
    daily, actions = _fixture("AAPL")
    tampered = daily.copy()
    tampered.loc[tampered["date"] < SPLIT_DAY, "adj_close"] *= 4
    report = ingest_prices(tmp_lake, FakePriceProvider(tampered, actions), ["AAPL"], START, END)
    assert report.symbols_ok == ("AAPL",)  # recorded, not fatal
    assert len(report.mismatches) == 20
    assert {m.symbol for m in report.mismatches} == {"AAPL"}


def test_ingest_fixtures_have_no_mismatches(tmp_lake: Lake) -> None:
    fake = FakeYFinance()
    provider = YFinanceProvider(download=fake.download, splits=fake.splits, sleep=lambda s: None)
    report = ingest_prices(tmp_lake, provider, ["AAPL", "SPY"], START, END)
    assert report.mismatches == ()
    assert report.symbols_ok == ("AAPL", "SPY")
