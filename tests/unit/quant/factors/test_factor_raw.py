"""Stage 16 Task 2 of the brief: each raw factor from fixture fundamentals, known values
(`raw_factors` is pure: panel + history + split-adjusted shares + market data)."""

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from factor_world import history_frame, panel_frame

from atlas.core.config import FactorsConfig
from atlas.quant.factors import FACTOR_NAMES, FACTORS, GROUPS, MARKET_COLUMNS, raw_factors

DAY = date(2024, 3, 1)
CFG = FactorsConfig()
NAN = math.nan

E0 = date(2023, 12, 31)
E1 = date(2022, 12, 31)  # 365 days before E0
E3 = date(2020, 12, 31)  # 1095 days before E0
QUARTER_ENDS = [
    date(2022, 3, 31),
    date(2022, 6, 30),
    date(2022, 9, 30),
    date(2022, 12, 31),
    date(2023, 3, 31),
    date(2023, 6, 30),
    date(2023, 9, 30),
    date(2023, 12, 31),
]
MARGINS = [0.30, 0.32, 0.31, 0.29, 0.33, 0.30, 0.28, 0.31]

PANEL = {
    "net_income_ttm": 50.0,
    "stockholders_equity": 400.0,
    "fcf_ttm": 30.0,
    "operating_income_ttm": 80.0,
    "depreciation_amortization_ttm": 20.0,
    "revenue_ttm": 1000.0,
    "gross_profit_ttm": 300.0,
    "total_assets": 2000.0,
    "operating_cash_flow_ttm": 70.0,
    "roe": 0.125,
    "roa": 0.025,
    "debt_to_equity": 0.5,
    "total_debt": 200.0,
    "cash_and_equivalents": 50.0,
}


def _history(symbol: str = "AAA") -> pd.DataFrame:
    """TTM revenue 800 (E3), 900 (E1), 1000 (E0); TTM EPS 2.0 (E1), 2.5 (E0); eight
    quarters of revenue 250 with gross margins `MARGINS`."""
    ttm_revenue = {E1: 900.0, E0: 1000.0}
    rows = [(symbol, "revenue", E3, NAN, 800.0)]
    for end, margin in zip(QUARTER_ENDS, MARGINS, strict=True):
        rows.append((symbol, "revenue", end, 250.0, ttm_revenue.get(end, NAN)))
        rows.append((symbol, "gross_profit", end, 250.0 * margin, NAN))
    rows += [(symbol, "eps_diluted", E1, NAN, 2.0), (symbol, "eps_diluted", E0, NAN, 2.5)]
    return history_frame(rows)


def _shares(symbol: str = "AAA") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": [symbol, symbol, symbol],
            "period_end": [date(2022, 7, 25), date(2023, 1, 20), date(2024, 1, 22)],
            "shares": [101.0, 100.0, 98.0],
        }
    )


def _market(symbols: list[str], cap: float = 1000.0, ev: float = 1150.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "price": 10.0,
            "price_date": date(2024, 2, 29),
            "shares": 100.0,
            "shares_date": date(2024, 1, 22),
            "shares_source": "reported",
            "market_cap": cap,
            "enterprise_value": ev,
        },
        index=pd.Index(symbols, name="symbol"),
    )[list(MARKET_COLUMNS)]


def _raw(**panel_overrides: float) -> pd.Series:
    panel = panel_frame({"AAA": PANEL | panel_overrides})
    out = raw_factors(
        panel,
        _history(),
        _shares(),
        _market(["AAA"]),
        day=DAY,
        config=CFG,
        max_staleness_days=400,
        max_quarter_days=100,
    )
    return out.loc["AAA"]


def test_specs() -> None:
    assert len(FACTORS) == 17
    assert tuple(f.name for f in FACTORS) == FACTOR_NAMES
    assert len(set(FACTOR_NAMES)) == 17
    assert GROUPS == ("value", "quality", "growth", "leverage", "size", "issuance")
    by_group = {g: [f.name for f in FACTORS if f.group == g] for g in GROUPS}
    assert by_group == {
        "value": ["earnings_yield", "book_yield", "fcf_yield", "ebit_ev", "sales_ev"],
        "quality": ["roe", "roa", "gross_profitability", "accruals", "margin_stability"],
        "growth": ["revenue_growth", "eps_growth", "revenue_cagr_3y"],
        "leverage": ["debt_to_equity", "net_debt_ebitda"],
        "size": ["size"],
        "issuance": ["net_issuance"],
    }
    assert all(f.description and f.unit for f in FACTORS)


def test_columns_and_dtype() -> None:
    panel = panel_frame({"AAA": PANEL, "BBB": {}})
    out = raw_factors(
        panel,
        _history(),
        _shares(),
        _market(["AAA", "BBB"]),
        day=DAY,
        config=CFG,
        max_staleness_days=400,
        max_quarter_days=100,
    )
    assert list(out.columns) == list(FACTOR_NAMES)
    assert list(out.index) == ["AAA", "BBB"]
    assert out.index.name == "symbol"
    assert (out.dtypes == "float64").all()
    assert not np.isinf(out.to_numpy()).any()


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("earnings_yield", 50 / 1000),
        ("book_yield", 400 / 1000),
        ("fcf_yield", 30 / 1000),
        ("ebit_ev", 80 / 1150),
        ("sales_ev", 1000 / 1150),
        ("roe", 0.125),
        ("roa", 0.025),
        ("gross_profitability", 300 / 2000),
        ("accruals", -(50 - 70) / 2000),
        ("margin_stability", -float(np.std(MARGINS, ddof=1))),
        ("revenue_growth", 1000 / 900 - 1),
        ("eps_growth", (2.5 - 2.0) / 2.0),
        ("revenue_cagr_3y", (1000 / 800) ** (1 / 3) - 1),
        ("debt_to_equity", 0.5),
        ("net_debt_ebitda", (200 - 50) / (80 + 20)),  # EBITDA = EBIT + D&A
        ("size", math.log(1000)),
        ("net_issuance", -(98 / 100 - 1)),
    ],
)
def test_known_value(name: str, expected: float) -> None:
    assert _raw()[name] == pytest.approx(expected, rel=1e-12)


def test_known_values_cover_every_factor() -> None:
    values = _raw()
    assert values.notna().all(), values[values.isna()]


@pytest.mark.parametrize(
    ("overrides", "nan_factors"),
    [
        ({"stockholders_equity": NAN}, ["book_yield"]),
        ({"total_assets": 0.0}, ["gross_profitability", "accruals"]),
        ({"operating_income_ttm": -20.0}, ["net_debt_ebitda"]),  # EBITDA = 0
        ({"operating_income_ttm": -25.0}, ["net_debt_ebitda"]),  # EBITDA < 0
        ({"operating_income_ttm": NAN}, ["ebit_ev", "net_debt_ebitda"]),
        ({"depreciation_amortization_ttm": NAN}, ["net_debt_ebitda"]),  # never EBIT alone
        ({"operating_cash_flow_ttm": NAN}, ["accruals"]),
        ({"total_debt": NAN}, ["net_debt_ebitda"]),
    ],
)
def test_missing_or_nonpositive_denominators(overrides: dict, nan_factors: list[str]) -> None:
    values = _raw(**overrides)
    for name in nan_factors:
        assert math.isnan(values[name]), name
    others = [n for n in FACTOR_NAMES if n not in nan_factors]
    assert values[others].notna().all()


def test_net_debt_ebitda_with_an_operating_loss_smaller_than_depreciation() -> None:
    """EBIT -5 and D&A 20: EBITDA is 15, positive, so the ratio exists (with EBIT alone it
    did not)."""
    values = _raw(operating_income_ttm=-5.0)
    assert values["net_debt_ebitda"] == pytest.approx((200 - 50) / 15)
    assert values["ebit_ev"] == pytest.approx(-5 / 1150)  # a negative EBIT yield is kept


def test_net_debt_ebitda_spec_names_ebitda() -> None:
    spec = next(f for f in FACTORS if f.name == "net_debt_ebitda")
    assert "EBITDA" in spec.description
    assert "proxy" not in spec.description


def test_no_market_cap_or_ev() -> None:
    panel = panel_frame({"AAA": PANEL})
    out = raw_factors(
        panel,
        _history(),
        _shares(),
        _market(["AAA"], cap=NAN, ev=-3.0),
        day=DAY,
        config=CFG,
        max_staleness_days=400,
        max_quarter_days=100,
    ).loc["AAA"]
    for name in ("earnings_yield", "book_yield", "fcf_yield", "size", "ebit_ev", "sales_ev"):
        assert math.isnan(out[name]), name
    assert out[["roe", "revenue_growth", "net_issuance"]].notna().all()


def test_negative_book_and_earnings_kept() -> None:
    values = _raw(stockholders_equity=-100.0, net_income_ttm=-20.0)
    assert values["book_yield"] == pytest.approx(-0.1)
    assert values["earnings_yield"] == pytest.approx(-0.02)


def _hist_with(
    drop: set[tuple[str, date]] = frozenset(), shift: dict | None = None
) -> pd.DataFrame:  # type: ignore[assignment]
    h = _history()
    keep = [(c, e) not in drop for c, e in zip(h["concept"], h["period_end"], strict=True)]
    h = h[keep].copy()
    for (concept, end), new_end in (shift or {}).items():
        hit = (h["concept"] == concept) & (h["period_end"] == end)
        h.loc[hit, "period_end"] = new_end
    return h


def _raw_hist(
    history: pd.DataFrame, shares: pd.DataFrame | None = None, day: date = DAY
) -> pd.Series:
    panel = panel_frame({"AAA": PANEL})
    sh = _shares() if shares is None else shares
    return raw_factors(
        panel,
        history,
        sh,
        _market(["AAA"]),
        day=day,
        config=CFG,
        max_staleness_days=400,
        max_quarter_days=100,
    ).loc["AAA"]


def test_year_ago_end_within_tolerance() -> None:
    # A 52/53-week year: the year-ago end 2023-01-01 is 364 days before E0, within 20 days.
    h = _hist_with(shift={("eps_diluted", E1): date(2023, 1, 1)})
    assert _raw_hist(h)["eps_growth"] == pytest.approx(0.25)
    far = _hist_with(shift={("eps_diluted", E1): date(2022, 12, 1)})  # 395 days: too far
    assert math.isnan(_raw_hist(far)["eps_growth"])


def test_growth_needs_both_ends() -> None:
    v = _raw_hist(_hist_with(drop={("revenue", E3)}))
    assert math.isnan(v["revenue_cagr_3y"]) and v["revenue_growth"] == pytest.approx(1 / 9)
    v = _raw_hist(_hist_with(drop={("eps_diluted", E1)}))
    assert math.isnan(v["eps_growth"])


def test_growth_denominators() -> None:
    h = _history()
    h.loc[(h["concept"] == "eps_diluted") & (h["period_end"] == E1), "ttm"] = 0.005
    assert math.isnan(_raw_hist(h)["eps_growth"])  # |year-ago EPS| < min_abs_eps
    h.loc[(h["concept"] == "eps_diluted") & (h["period_end"] == E1), "ttm"] = -2.0
    assert _raw_hist(h)["eps_growth"] == pytest.approx((2.5 + 2.0) / 2.0)  # over |-2|
    h = _history()
    h.loc[(h["concept"] == "revenue") & (h["period_end"] == E3), "ttm"] = 0.0
    assert math.isnan(_raw_hist(h)["revenue_cagr_3y"])


def test_stale_latest_period_gives_no_growth() -> None:
    late = E0 + timedelta(days=401)
    v = _raw_hist(_history(), day=late)
    for name in ("revenue_growth", "eps_growth", "revenue_cagr_3y", "margin_stability"):
        assert math.isnan(v[name]), name


def test_margin_stability_needs_every_quarter() -> None:
    v = _raw_hist(_hist_with(drop={("gross_profit", QUARTER_ENDS[2])}))
    assert math.isnan(v["margin_stability"])
    h = _history()
    h.loc[(h["concept"] == "revenue") & (h["period_end"] == QUARTER_ENDS[0]), "value"] = 0.0
    assert math.isnan(_raw_hist(h)["margin_stability"])


def test_margin_stability_uses_the_last_quarters() -> None:
    extra = history_frame(
        [
            ("AAA", "revenue", date(2021, 12, 31), 100.0, NAN),
            ("AAA", "gross_profit", date(2021, 12, 31), 90.0, NAN),
        ]
    )
    v = _raw_hist(pd.concat([_history(), extra], ignore_index=True))
    assert v["margin_stability"] == pytest.approx(-float(np.std(MARGINS, ddof=1)))


def test_margin_stability_rejects_a_gap() -> None:
    # 8 quarters, but one is a year older: the span exceeds 8 quarters.
    h = _hist_with(
        shift={
            ("revenue", QUARTER_ENDS[0]): date(2021, 3, 31),
            ("gross_profit", QUARTER_ENDS[0]): date(2021, 3, 31),
        }
    )
    assert math.isnan(_raw_hist(h)["margin_stability"])


def test_issuance_year_ago_count() -> None:
    v = _raw_hist(_history())
    assert v["net_issuance"] == pytest.approx(0.02)
    sh = _shares().iloc[[0, 2]]  # no count near 2023-01-22
    assert math.isnan(_raw_hist(_history(), shares=sh)["net_issuance"])
    old = _shares().copy()
    old["period_end"] = [date(2021, 7, 25), date(2022, 1, 20), date(2022, 1, 22)]
    assert math.isnan(_raw_hist(_history(), shares=old)["net_issuance"])  # latest is stale


def test_symbols_without_history() -> None:
    panel = panel_frame({"AAA": PANEL, "ZZZ": PANEL})
    out = raw_factors(
        panel,
        _history(),
        _shares(),
        _market(["AAA", "ZZZ"]),
        day=DAY,
        config=CFG,
        max_staleness_days=400,
        max_quarter_days=100,
    )
    z = out.loc["ZZZ"]
    for name in (
        "revenue_growth",
        "eps_growth",
        "revenue_cagr_3y",
        "margin_stability",
        "net_issuance",
    ):
        assert math.isnan(z[name])
    assert z["earnings_yield"] == pytest.approx(0.05)


def test_margin_span_limit_follows_quarter_days() -> None:
    # The eight quarters may span at most 7 x max_quarter_days + tolerance days (Stage 16
    # audit: the limit comes from data.fundamentals.quarter_days, not a constant in code).
    first = E0 - timedelta(days=7 * 92 + 20 + 10)  # 674 days: inside 7 x 100 + 20, not 7 x 92 + 20
    h = _hist_with(
        shift={("revenue", QUARTER_ENDS[0]): first, ("gross_profit", QUARTER_ENDS[0]): first}
    )
    panel = panel_frame({"AAA": PANEL})

    def stability(max_quarter_days: int) -> float:
        out = raw_factors(
            panel,
            h,
            _shares(),
            _market(["AAA"]),
            day=DAY,
            config=CFG,
            max_staleness_days=400,
            max_quarter_days=max_quarter_days,
        )
        return float(out.loc["AAA", "margin_stability"])

    assert not math.isnan(stability(100))
    assert math.isnan(stability(92))
