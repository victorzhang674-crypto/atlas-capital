"""Stage 16 Task 1 of the brief: market cap and enterprise value from point-in-time shares
(`adjusted_shares`, `compute_market`; `market_data` reads them through `DataView`)."""

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest
from factor_world import history_frame, panel_frame, prices_frame, splits_frame

from atlas.quant.factors import MARKET_COLUMNS, adjusted_shares, compute_market

DAY = date(2024, 3, 1)
SESSIONS = [
    date(2024, 1, 19),
    date(2024, 1, 22),
    date(2024, 2, 14),
    date(2024, 2, 15),
    date(2024, 2, 28),
    date(2024, 2, 29),
]


def _shares(*rows: tuple[str, date, float]) -> pd.DataFrame:
    return history_frame([(s, "shares_outstanding", d, v, math.nan) for s, d, v in rows])


def test_adjusted_shares_restates_a_later_split() -> None:
    # AAA's count is dated 2024-01-20 (a Saturday); a 2:1 split went ex on 2024-02-15.
    splits = splits_frame(SESSIONS, {"AAA": [0.5, 0.5, 0.5, 1, 1, 1]}, symbols=["BBB"])
    hist = _shares(
        ("AAA", date(2024, 1, 20), 20.0),
        ("AAA", date(2024, 2, 20), 41.0),
        ("BBB", date(2024, 1, 22), 7.0),
    )
    out = adjusted_shares(hist, splits)
    assert list(out.columns) == ["symbol", "period_end", "shares"]
    got = {(r.symbol, r.period_end): r.shares for r in out.itertuples()}
    assert got == {
        ("AAA", date(2024, 1, 20)): 40.0,  # last session on or before it: 01-19, factor 0.5
        ("AAA", date(2024, 2, 20)): 41.0,
        ("BBB", date(2024, 1, 22)): 7.0,
    }


def test_adjusted_shares_before_the_window_uses_its_first_session() -> None:
    splits = splits_frame(SESSIONS, {"AAA": [0.5, 0.5, 0.5, 1, 1, 1]})
    out = adjusted_shares(_shares(("AAA", date(2023, 12, 1), 10.0)), splits)
    assert out["shares"].iloc[0] == 20.0


def test_adjusted_shares_ignores_other_concepts_and_empty() -> None:
    hist = history_frame([("AAA", "revenue", date(2023, 12, 31), 5.0, 20.0)])
    out = adjusted_shares(hist, splits_frame(SESSIONS, symbols=["AAA"]))
    assert out.empty
    assert list(out.columns) == ["symbol", "period_end", "shares"]


def _market(**kw: object) -> pd.DataFrame:
    panel = panel_frame(
        {
            "AAA": {"total_debt": 300.0, "cash_and_equivalents": 100.0},
            "BBB": {"total_debt": 0.0, "cash_and_equivalents": 10.0},
            "CCC": {"cash_and_equivalents": 10.0},  # no debt tag: EV unknown
            "DDD": {"total_debt": 1.0, "cash_and_equivalents": 1.0},
        }
    )
    prices = prices_frame(
        [
            ("AAA", date(2024, 2, 28), 49.0),
            ("AAA", date(2024, 2, 29), 50.0),
            ("BBB", date(2024, 2, 28), 5.0),
            ("CCC", date(2024, 2, 29), 2.0),
        ]
    )
    splits = splits_frame(
        SESSIONS, {"AAA": [0.5, 0.5, 0.5, 1, 1, 1]}, symbols=["BBB", "CCC", "DDD"]
    )
    shares = adjusted_shares(
        _shares(
            ("AAA", date(2024, 1, 20), 20.0),
            ("BBB", date(2024, 1, 22), 7.0),
            ("BBB", date(2023, 1, 22), 6.0),
            ("CCC", date(2022, 12, 1), 3.0),
            ("DDD", date(2024, 1, 22), 3.0),
        ),
        splits,
    )
    args: dict = {"day": DAY, "max_staleness_days": 400} | kw
    return compute_market(prices, shares, splits, panel, **args)


def test_market_cap_and_ev_known_values() -> None:
    m = _market()
    assert list(m.columns) == list(MARKET_COLUMNS)
    assert list(m.index) == ["AAA", "BBB", "CCC", "DDD"]
    aaa = m.loc["AAA"]
    assert (aaa["price"], aaa["price_date"]) == (50.0, date(2024, 2, 29))
    assert (aaa["shares"], aaa["shares_date"]) == (40.0, date(2024, 1, 20))
    assert aaa["market_cap"] == 2000.0
    assert aaa["enterprise_value"] == 2000.0 + 300.0 - 100.0
    bbb = m.loc["BBB"]
    assert (bbb["price"], bbb["shares"], bbb["market_cap"]) == (5.0, 7.0, 35.0)  # latest count
    assert bbb["enterprise_value"] == 35.0 - 10.0


def test_missing_debt_gives_no_ev_and_stale_shares_no_cap() -> None:
    m = _market()
    ccc = m.loc["CCC"]  # its only count is 456 days old (> 400): no shares, no cap
    assert math.isnan(ccc["shares"]) and math.isnan(ccc["market_cap"])
    assert math.isnan(ccc["enterprise_value"])
    ddd = m.loc["DDD"]  # no bar in the price window
    assert math.isnan(ddd["price"]) and ddd["price_date"] is None
    assert math.isnan(ddd["market_cap"]) and ddd["shares"] == 3.0


def test_ev_needs_cash_too() -> None:
    panel = panel_frame({"AAA": {"total_debt": 5.0}})
    prices = prices_frame([("AAA", date(2024, 2, 29), 10.0)])
    splits = splits_frame(SESSIONS, symbols=["AAA"])
    shares = adjusted_shares(_shares(("AAA", date(2024, 1, 22), 2.0)), splits)
    m = compute_market(prices, shares, splits, panel, day=DAY, max_staleness_days=400)
    assert m.loc["AAA", "market_cap"] == 20.0
    assert math.isnan(m.loc["AAA", "enterprise_value"])


def test_price_before_a_split_uses_pre_split_shares() -> None:
    # The latest bar (2024-02-14) predates the 2:1 split of 02-15: price and shares must be
    # on the same basis, so the cap uses the count in 02-14 shares (20), not today's (40).
    panel = panel_frame({"AAA": {"total_debt": 0.0, "cash_and_equivalents": 0.0}})
    prices = prices_frame([("AAA", date(2024, 2, 14), 100.0)])
    splits = splits_frame(SESSIONS, {"AAA": [0.5, 0.5, 0.5, 1, 1, 1]})
    shares = adjusted_shares(_shares(("AAA", date(2024, 1, 20), 20.0)), splits)
    m = compute_market(prices, shares, splits, panel, day=DAY, max_staleness_days=400)
    assert m.loc["AAA", "shares"] == 40.0  # today's shares
    assert m.loc["AAA", "market_cap"] == pytest.approx(2000.0)


def test_nonpositive_inputs_give_nan() -> None:
    panel = panel_frame({"AAA": {"total_debt": 0.0, "cash_and_equivalents": 0.0}})
    prices = prices_frame([("AAA", date(2024, 2, 29), 10.0)])
    splits = splits_frame(SESSIONS, symbols=["AAA"])
    shares = adjusted_shares(_shares(("AAA", date(2024, 1, 22), 0.0)), splits)
    m = compute_market(prices, shares, splits, panel, day=DAY, max_staleness_days=400)
    assert np.isnan(m.loc["AAA", "market_cap"])
