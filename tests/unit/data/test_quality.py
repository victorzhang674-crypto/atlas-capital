"""Stage 13: data quality checks, the quality report and the persisted issues table."""

from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest
from view_helpers import (
    CAL,
    QUARTERS,
    business_days,
    macro_rows,
    make_actions,
    make_bars,
    make_facts,
    month_starts,
    no_actions,
    quarterly_facts,
    repo_concept_map,
    sessions_ending,
    utc,
    walk,
)

from atlas.core.config import AtlasConfig, FundamentalsConfig, QualityConfig
from atlas.core.errors import LookAheadError
from atlas.core.pit import AsOf
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.quality import (
    CHECKS,
    DATA_QUALITY_ISSUES,
    ISSUE_COLUMNS,
    QualityIssue,
    Severity,
    blocked_symbols,
    check_fundamentals_jumps,
    check_fundamentals_period_after_filing,
    check_macro_gaps,
    check_missing_sessions,
    check_nonpositive_prices,
    check_ohlc,
    check_return_outliers,
    check_stale_prices,
    check_vendor_mix,
    infer_frequency,
    issues_frame,
    latest_known_session,
    merge_issues,
    quality_report,
    read_quality_issues,
)

END = date(2024, 2, 29)  # the last session known at the 2024-03-01 08:00 cutoff
W = Severity.WARNING
C = Severity.CRITICAL

# --- Task 2: the issue contract and its table --------------------------------------------------


def test_issue_table_registered() -> None:
    assert DEFAULT_REGISTRY.get("data_quality_issues") is DATA_QUALITY_ISSUES
    assert DATA_QUALITY_ISSUES.primary_key == ("report_day", "table", "symbol", "check", "date")
    assert set(ISSUE_COLUMNS) <= set(DATA_QUALITY_ISSUES.column_names)
    assert CHECKS == (
        "missing_sessions",
        "stale_price",
        "return_outlier",
        "ohlc_inconsistent",
        "nonpositive_price",
        "split_mismatch",
        "fundamentals_jump",
        "macro_gap",
        "vendor_mix",
        "fundamentals_period_after_filing",
    )


def test_issues_frame_columns_and_types() -> None:
    issue = QualityIssue(
        table="prices_daily",
        symbol="AAA",
        date=date(2024, 2, 28),
        check="stale_price",
        severity=Severity.CRITICAL,
        detail="close unchanged",
        available_at=utc(2024, 2, 28, 21, 15),
    )
    frame = issues_frame([issue])
    assert list(frame.columns) == list(ISSUE_COLUMNS)
    assert frame.loc[0, "severity"] == "critical"
    assert frame.loc[0, "date"] == date(2024, 2, 28)
    assert str(frame["available_at"].dtype) == "datetime64[us, UTC]"
    empty = issues_frame([])
    assert list(empty.columns) == list(ISSUE_COLUMNS) and empty.empty
    assert str(empty["available_at"].dtype) == "datetime64[us, UTC]"


def test_issue_rejects_naive_available_at() -> None:
    with pytest.raises(ValueError, match="naive"):
        QualityIssue(
            table="prices_daily",
            symbol="AAA",
            date=date(2024, 2, 28),
            check="stale_price",
            severity=Severity.CRITICAL,
            detail="x",
            available_at=datetime(2024, 2, 28, 21, 15),
        )
    with pytest.raises(ValueError, match="check"):
        QualityIssue(
            table="prices_daily",
            symbol="AAA",
            date=date(2024, 2, 28),
            check="nonsense",
            severity=Severity.CRITICAL,
            detail="x",
            available_at=utc(2024, 2, 28),
        )


def test_severity_order() -> None:
    assert Severity.INFO < Severity.WARNING < Severity.CRITICAL
    assert Severity("critical") is Severity.CRITICAL
    assert isinstance(pd.Series([Severity.WARNING.value]).iloc[0], str)


# --- Task 3: price checks ----------------------------------------------------------------------

SINCE = sessions_ending(END, 21)[0]


def _drop(frame: pd.DataFrame, day: date) -> pd.DataFrame:
    return frame[frame["date"] != day].reset_index(drop=True)


def test_missing_sessions_flags_gap() -> None:
    days = sessions_ending(END, 30)
    bars = _drop(make_bars("AAA", walk(30), end=END), days[-5])
    issues = check_missing_sessions(bars, CAL, since=SINCE, severity=W)
    assert [(i.symbol, i.date, i.check, i.severity) for i in issues] == [
        ("AAA", days[-5], "missing_sessions", W)
    ]
    assert issues[0].table == "prices_daily"
    # Evidence of the gap: the next bar after it.
    nxt = bars.loc[bars["date"] == days[-4], "available_at"].iloc[0]
    assert issues[0].available_at == nxt.to_pydatetime()


def test_missing_sessions_ignores_before_first_bar_and_outside_window() -> None:
    days = sessions_ending(END, 60)
    late_listing = make_bars("NEW", walk(5), end=END)  # first bar 5 sessions ago: no gap
    old_gap = _drop(make_bars("OLD", walk(60), end=END), days[10])  # gap before the window
    bars = pd.concat([late_listing, old_gap], ignore_index=True)
    assert check_missing_sessions(bars, CAL, since=SINCE, severity=W) == []


@pytest.mark.parametrize(
    ("field", "value", "word"),
    [
        ("high", 99.0, "high"),  # high below close 100
        ("low", 101.0, "low"),  # low above close 100
        ("open", float("nan"), "missing"),
        ("volume", -5, "volume"),
    ],
)
def test_ohlc_inconsistent_cases(field: str, value: float, word: str) -> None:
    bars = make_bars("AAA", [100.0] * 25, end=END)
    bars.loc[bars.index[-3], field] = value
    issues = check_ohlc(bars, since=SINCE, severity=C)
    assert [(i.symbol, i.date, i.check, i.severity) for i in issues] == [
        ("AAA", bars["date"].iloc[-3], "ohlc_inconsistent", C)
    ]
    assert word in issues[0].detail


def test_nonpositive_price() -> None:
    bars = make_bars("AAA", walk(25), end=END)
    bars.loc[bars.index[-1], ["open", "low", "close"]] = [0.0, -1.0, 0.0]
    issues = check_nonpositive_prices(bars, since=SINCE, severity=C)
    assert [(i.symbol, i.date, i.check) for i in issues] == [("AAA", END, "nonpositive_price")]
    assert "close" in issues[0].detail and "low" in issues[0].detail


def test_stale_price_run() -> None:
    closes = walk(30)[:24] + [55.5] * 6  # 6 equal closes = 5 unchanged sessions
    bars = make_bars("AAA", closes, end=END)
    issues = check_stale_prices(bars, since=SINCE, min_sessions=5, severity=C)
    assert [(i.symbol, i.date, i.check, i.severity) for i in issues] == [
        ("AAA", END, "stale_price", C)
    ]
    assert "5" in issues[0].detail


def test_stale_price_needs_volume() -> None:
    closes = walk(30)[:24] + [55.5] * 6
    vols = [1_000] * 27 + [0, 1_000, 1_000]  # a zero-volume bar breaks the run
    bars = make_bars("AAA", closes, end=END, volume=vols)
    assert check_stale_prices(bars, since=SINCE, min_sessions=5, severity=C) == []


def test_stale_price_short_run_ok() -> None:
    closes = walk(30)[:25] + [55.5] * 5  # only 4 unchanged sessions
    bars = make_bars("AAA", closes, end=END)
    assert check_stale_prices(bars, since=SINCE, min_sessions=5, severity=C) == []


def test_vendor_mix() -> None:
    vendors = ["yfinance"] * 25 + ["alpaca"] * 5
    bars = make_bars("AAA", walk(30), end=END, vendor=vendors)
    days = sessions_ending(END, 30)
    issues = check_vendor_mix(bars, since=SINCE, severity=W)
    assert [(i.symbol, i.date, i.check) for i in issues] == [("AAA", days[25], "vendor_mix")]
    assert "alpaca" in issues[0].detail and "yfinance" in issues[0].detail
    # A vendor switch before the window is not reported.
    old = make_bars("BBB", walk(60), end=END, vendor=["alpaca"] * 5 + ["yfinance"] * 55)
    assert check_vendor_mix(old, since=SINCE, severity=W) == []


def test_clean_bars_have_no_issues() -> None:
    bars = pd.concat(
        [make_bars(s, walk(80, seed=i), end=END) for i, s in enumerate(["AAA", "BBB"])],
        ignore_index=True,
    )
    assert check_missing_sessions(bars, CAL, since=SINCE, severity=W) == []
    assert check_ohlc(bars, since=SINCE, severity=C) == []
    assert check_nonpositive_prices(bars, since=SINCE, severity=C) == []
    assert check_stale_prices(bars, since=SINCE, min_sessions=5, severity=C) == []
    assert check_vendor_mix(bars, since=SINCE, severity=W) == []


# --- Task 4: return outliers and split mismatches ----------------------------------------------

QC = QualityConfig()


def _scaled(closes: list[float], at: int, factor: float) -> list[float]:
    """`closes` with every close from index `at` on multiplied by `factor` (a jump)."""
    return closes[:at] + [c * factor for c in closes[at:]]


def _found(issues: list[QualityIssue]) -> list[tuple[str, date, str, Severity]]:
    return [(i.symbol, i.date, i.check, i.severity) for i in issues]


def test_return_outlier_flagged() -> None:
    days = sessions_ending(END, 90)
    bars = make_bars("AAA", _scaled(walk(90), 87, 1.25), end=END)  # +25% at a 1% daily vol
    issues = check_return_outliers(bars, no_actions(), since=SINCE, config=QC)
    assert _found(issues) == [("AAA", days[87], "return_outlier", W)]
    assert issues[0].table == "prices_daily"
    assert "sigma" in issues[0].detail


def test_return_outlier_not_on_action_day() -> None:
    days = sessions_ending(END, 90)
    closes = _scaled(walk(90), 87, 1.25)
    bars = make_bars("AAA", closes, end=END)
    actions = make_actions(("AAA", days[87], "dividend", None, 0.2))  # a real ex-dividend day
    assert check_return_outliers(bars, actions, since=SINCE, config=QC) == []


def test_recorded_split_explains_move() -> None:
    days = sessions_ending(END, 90)
    bars = make_bars("AAA", _scaled(walk(90), 85, 0.5), end=END)  # raw closes halve
    actions = make_actions(("AAA", days[85], "split", 2.0, None))
    assert check_return_outliers(bars, actions, since=SINCE, config=QC) == []


def test_wrong_split_ratio_is_mismatch() -> None:
    days = sessions_ending(END, 90)
    bars = make_bars("AAA", _scaled(walk(90), 85, 0.25), end=END)  # a 4:1 split ...
    actions = make_actions(("AAA", days[85], "split", 2.0, None))  # ... recorded as 2:1
    issues = check_return_outliers(bars, actions, since=SINCE, config=QC)
    assert _found(issues) == [("AAA", days[85], "split_mismatch", C)]
    assert issues[0].table == "corporate_actions"
    assert "does not explain" in issues[0].detail


@pytest.mark.parametrize(("factor", "word"), [(0.5, "2-for-1"), (10.0, "1-for-10")])
def test_unrecorded_split_is_mismatch(factor: float, word: str) -> None:
    days = sessions_ending(END, 90)
    bars = make_bars("AAA", _scaled(walk(90), 85, factor), end=END)
    issues = check_return_outliers(bars, no_actions(), since=SINCE, config=QC)
    assert _found(issues) == [("AAA", days[85], "split_mismatch", C)]
    assert word in issues[0].detail


def test_bad_dividend_is_mismatch() -> None:
    days = sessions_ending(END, 90)
    aaa = make_bars("AAA", walk(90, seed=1), end=END)
    bbb = make_bars("BBB", walk(90, seed=2), end=END)
    ccc = make_bars("CCC", walk(90, seed=3), end=END)
    actions = make_actions(
        ("AAA", days[80], "dividend", None, 1_000.0),  # at or above the prior close
        ("BBB", days[82], "dividend", None, 0.25 * float(bbb["close"].iloc[81])),  # 25%: bogus
        ("CCC", days[84], "dividend", None, 0.5),  # a normal dividend
    )
    bars = pd.concat([aaa, bbb, ccc], ignore_index=True)
    issues = check_return_outliers(bars, actions, since=SINCE, config=QC)
    assert _found(issues) == [
        ("AAA", days[80], "split_mismatch", C),
        ("BBB", days[82], "split_mismatch", C),
    ]
    assert "not below the prior close" in issues[0].detail


def test_sigma_floor() -> None:
    quiet = walk(90, vol=0.0001)
    small = make_bars("AAA", _scaled(quiet, 87, 1.03), end=END)  # 3% < 8 x 0.5% floor
    big = make_bars("BBB", _scaled(quiet, 87, 1.05), end=END)  # 5% > 4%
    bars = pd.concat([small, big], ignore_index=True)
    issues = check_return_outliers(bars, no_actions(), since=SINCE, config=QC)
    assert [(i.symbol, i.check) for i in issues] == [("BBB", "return_outlier")]


def test_outlier_needs_history() -> None:
    bars = make_bars("AAA", _scaled(walk(25), 15, 1.5), end=END)  # only 14 earlier returns
    assert check_return_outliers(bars, no_actions(), since=SINCE, config=QC) == []


# --- Task 5: fundamentals jumps ----------------------------------------------------------------

AS_OF = AsOf.for_session(date(2024, 3, 1))
FC = FundamentalsConfig()


def _jumps(facts: pd.DataFrame, as_of: AsOf = AS_OF) -> list[QualityIssue]:
    return check_fundamentals_jumps(
        facts, as_of, since=SINCE, concept_map=repo_concept_map(), config=FC,
        jump_ratio=10.0, severity=W,
    )  # fmt: skip


def test_fundamentals_jump_flow_quarter() -> None:
    facts = quarterly_facts("AAA", "Revenues", [100e6, 105e6, 110e6, 1.2e9])
    issues = _jumps(facts)
    assert [(i.table, i.symbol, i.date, i.check, i.severity) for i in issues] == [
        ("fundamentals_facts", "AAA", QUARTERS[3][2], "fundamentals_jump", W)
    ]
    assert "revenue" in issues[0].detail and "10.9x" in issues[0].detail
    assert issues[0].available_at == utc(2024, 2, 15, 21)


def test_fundamentals_jump_balance() -> None:
    facts = quarterly_facts("AAA", "Assets", [1e9, 1.05e9, 1.1e9, 5e7], instant=True)
    issues = _jumps(facts)
    assert [(i.symbol, i.check) for i in issues] == [("AAA", "fundamentals_jump")]
    assert "total_assets" in issues[0].detail and "22.0x" in issues[0].detail


def test_fundamentals_no_jump_below_ratio() -> None:
    facts = pd.concat(
        [
            quarterly_facts("AAA", "Revenues", [100e6, 105e6, 110e6, 990e6]),  # 9x
            quarterly_facts("BBB", "Assets", [1e9, 1e9, 1e9, 1.2e8], instant=True),  # 1/8.3
        ],
        ignore_index=True,
    )
    assert _jumps(facts) == []


def test_fundamentals_jump_zero_previous_skipped() -> None:
    facts = quarterly_facts("AAA", "Revenues", [100e6, 105e6, 0.0, 50e6])
    assert _jumps(facts) == []


def test_fundamentals_jump_only_new_filings() -> None:
    old = quarterly_facts("AAA", "Revenues", [100e6, 2e9, 2.1e9, 2.2e9])  # jump filed 2023-08
    assert _jumps(old) == []
    late = quarterly_facts("BBB", "Revenues", [100e6, 105e6, 110e6, 1.2e9])
    late.loc[late.index[-1], ["available_at", "accepted_at"]] = pd.Timestamp(
        "2024-03-01 14:00", tz="UTC"
    )
    assert _jumps(late) == []  # filed after the 08:00 cutoff: not known yet


def test_period_after_filing_reported() -> None:
    # Phase 1 checkpoint, Tier 3: a fact dated after its own filing (AAL's 2027-07-17 cover-page
    # share count in a 2026-07-23 10-Q) is a filer error; `known_facts` leaves it out and the
    # quality report says so, one issue per symbol and filing day.
    filed = date(2024, 2, 12)
    facts = pd.concat(
        [
            make_facts(
                "AAA",
                {"tag": "dei:EntityCommonStockSharesOutstanding", "value": 5e8,
                 "end": date(2025, 2, 10), "filed": filed},
                {"tag": "Assets", "value": 1e9, "end": date(2024, 6, 30), "filed": filed},
                {"tag": "Assets", "value": 1e9, "end": date(2023, 12, 31), "filed": filed},
            ),
            make_facts(  # filed before the window: an old error, already reported then
                "BBB",
                {"tag": "Assets", "value": 1e9, "end": date(2025, 1, 1),
                 "filed": date(2023, 5, 1)},
            ),
        ],
        ignore_index=True,
    )  # fmt: skip
    issues = check_fundamentals_period_after_filing(facts, AS_OF, since=SINCE, severity=W)
    assert [(i.table, i.symbol, i.date, i.check, i.severity) for i in issues] == [
        ("fundamentals_facts", "AAA", date(2024, 2, 12), "fundamentals_period_after_filing", W)
    ]
    assert "2025-02-10" in issues[0].detail and "2024-06-30" in issues[0].detail
    assert issues[0].available_at == utc(2024, 2, 12, 21)


def test_period_after_filing_respects_the_cutoff() -> None:
    late = make_facts(
        "AAA",
        {"tag": "Assets", "value": 1e9, "end": date(2025, 1, 1), "filed": date(2024, 3, 1),
         "available_at": utc(2024, 3, 1, 14)},
    )  # fmt: skip
    assert check_fundamentals_period_after_filing(late, AS_OF, since=SINCE, severity=W) == []


@pytest.mark.parametrize(
    ("stamp", "reported"),
    [
        (utc(2024, 1, 31, 4, 59), False),  # 23:59 New York on 2024-01-30: before the window
        (utc(2024, 1, 31, 5, 0), True),  # 00:00 New York on 2024-01-31, the first window day
    ],
)
def test_fundamentals_jump_window_boundary_new_york_midnight(
    stamp: datetime, reported: bool
) -> None:
    assert sessions_ending(END, 21)[0] == date(2024, 1, 31)  # the window's first day
    facts = quarterly_facts("AAA", "Revenues", [100e6, 105e6, 110e6, 1.2e9])
    facts.loc[facts.index[-1], ["available_at", "accepted_at"]] = pd.Timestamp(stamp)
    assert bool(_jumps(facts)) is reported


# --- Task 6: macro gaps ------------------------------------------------------------------------

GAP = QC.macro_max_gap_days
AGE = QC.macro_max_age_days


def _gaps(observations: pd.DataFrame) -> list[QualityIssue]:
    return check_macro_gaps(observations, AS_OF, since=SINCE, max_gap=GAP, max_age=AGE, severity=W)


def test_infer_frequency() -> None:
    assert infer_frequency(business_days(date(2024, 1, 1), date(2024, 2, 29))) == "daily"
    weekly = [date(2024, 1, 6) + timedelta(weeks=i) for i in range(8)]
    assert infer_frequency(weekly) == "weekly"
    assert infer_frequency(month_starts(date(2023, 1, 1), date(2024, 1, 1))) == "monthly"
    quarters = [date(2022, 1, 1), date(2022, 4, 1), date(2022, 7, 1), date(2022, 10, 1)]
    assert infer_frequency(quarters) == "quarterly"
    assert infer_frequency([date(2020, 1, 1), date(2021, 1, 1), date(2022, 1, 1)]) == "annual"
    assert infer_frequency([date(2024, 1, 1), date(2024, 1, 2)]) is None


def test_macro_internal_gap() -> None:
    days = [
        d
        for d in business_days(date(2023, 12, 1), date(2024, 2, 28))
        if not date(2024, 2, 5) <= d <= date(2024, 2, 16)
    ]
    issues = _gaps(macro_rows("DGS10", days))
    assert [(i.table, i.symbol, i.date, i.check, i.severity) for i in issues] == [
        ("macro_observations", "DGS10", date(2024, 2, 19), "macro_gap", W)
    ]
    assert "2024-02-02" in issues[0].detail and "17 days" in issues[0].detail


def test_macro_stale_tail() -> None:
    cpi = macro_rows(
        "CPIAUCSL",
        month_starts(date(2023, 1, 1), date(2023, 10, 1)),
        available=lambda d: datetime(d.year, d.month, 12, 13, 30, tzinfo=UTC) + timedelta(days=31),
    )
    issues = _gaps(cpi)
    assert [(i.symbol, i.date, i.check) for i in issues] == [("CPIAUCSL", AS_OF.day, "macro_gap")]
    assert "2023-10-01" in issues[0].detail and "152 days" in issues[0].detail
    assert issues[0].available_at == cpi["available_at"].max().to_pydatetime()


def test_macro_regular_series_ok() -> None:
    daily = macro_rows("DGS10", business_days(date(2023, 12, 1), date(2024, 2, 29)))
    monthly = macro_rows(
        "CPIAUCSL",
        month_starts(date(2023, 1, 1), date(2024, 1, 1)),
        available=lambda d: datetime(d.year, d.month, 12, 13, 30, tzinfo=UTC) + timedelta(days=31),
    )
    assert _gaps(pd.concat([daily, monthly], ignore_index=True)) == []


def test_macro_gap_uses_known_vintages_only() -> None:
    days = business_days(date(2023, 12, 1), date(2024, 2, 29))
    late = macro_rows("DGS10", days)
    late.loc[late["obs_date"] > date(2024, 2, 1), "available_at"] = pd.Timestamp(
        "2024-03-05 11:00", tz="UTC"
    )
    issues = _gaps(late)
    assert [(i.symbol, i.date) for i in issues] == [("DGS10", AS_OF.day)]
    assert "2024-02-01" in issues[0].detail


# --- Task 7: the report, persistence and the CRITICAL block -------------------------------------


def _write(lake: Lake, table: str, frame: pd.DataFrame) -> None:
    lake.write_table(table, frame, mode="upsert")


def _report(lake: Lake, as_of: AsOf | date = AS_OF, **kwargs: object) -> pd.DataFrame:
    return quality_report(lake, as_of, concept_map=repo_concept_map(), **kwargs)  # type: ignore[arg-type]


def _pairs(report: pd.DataFrame) -> set[tuple[str, str, str]]:
    return set(zip(report["table"], report["symbol"], report["check"], strict=True))


def test_latest_known_session() -> None:
    assert latest_known_session(AS_OF, CAL) == date(2024, 2, 29)  # 03-01's bar is unknown at 08:00
    after_close = AsOf(day=date(2024, 3, 1), cutoff=utc(2024, 3, 1, 22, 0))
    assert latest_known_session(after_close, CAL) == date(2024, 3, 1)
    weekend = AsOf.for_session(date(2024, 3, 3))
    assert latest_known_session(weekend, CAL) == date(2024, 3, 1)


def test_report_finds_each_table(tmp_lake: Lake) -> None:
    zero = walk(90, seed=2)
    zero[-2] = 0.0
    bars = pd.concat(
        [
            make_bars("AAA", walk(90, seed=1), end=END),
            make_bars("BAD", zero, end=END),
            make_bars("STL", walk(84, seed=3) + [50.0] * 6, end=END),
        ],
        ignore_index=True,
    )
    _write(tmp_lake, "prices_daily", bars)
    _write(tmp_lake, "corporate_actions", no_actions())
    _write(tmp_lake, "fundamentals_facts", quarterly_facts("AAA", "Revenues", [1e8, 1e8, 1e8, 2e9]))
    cpi = macro_rows("CPIAUCSL", month_starts(date(2023, 1, 1), date(2023, 10, 1)))
    _write(tmp_lake, "macro_observations", cpi)
    report = _report(tmp_lake)
    assert list(report.columns) == list(ISSUE_COLUMNS)
    assert {
        ("prices_daily", "BAD", "nonpositive_price"),
        ("prices_daily", "STL", "stale_price"),
        ("fundamentals_facts", "AAA", "fundamentals_jump"),
        ("macro_observations", "CPIAUCSL", "macro_gap"),
    } <= _pairs(report)
    assert blocked_symbols(report) == frozenset({"BAD", "STL"})
    assert (report["available_at"] < pd.Timestamp(AS_OF.cutoff)).all()


def test_report_window_limits_issues(tmp_lake: Lake) -> None:
    closes = walk(90)
    closes[-30] = 0.0  # 30 sessions back: outside the 21-session window
    _write(tmp_lake, "prices_daily", make_bars("OLD", closes, end=END))
    report = _report(tmp_lake)
    assert "nonpositive_price" not in set(report["check"])
    wide = _report(
        tmp_lake, config=AtlasConfig.model_validate({"data": {"quality": {"window_sessions": 40}}})
    )
    assert ("prices_daily", "OLD", "nonpositive_price") in _pairs(wide)


def test_report_ignores_unknown_rows(tmp_lake: Lake) -> None:
    days = sessions_ending(date(2024, 3, 1), 90)
    closes = walk(90)
    closes[-1] = 0.0  # the 2024-03-01 bar: known only at 16:15 that day
    _write(tmp_lake, "prices_daily", make_bars("AAA", closes, end=days[-1]))
    assert _report(tmp_lake).empty
    later = _report(tmp_lake, date(2024, 3, 4))
    assert ("prices_daily", "AAA", "nonpositive_price") in _pairs(later)


def test_report_severity_from_config(tmp_lake: Lake) -> None:
    closes = walk(90)
    closes = closes[:87] + [c * 1.3 for c in closes[87:]]
    _write(tmp_lake, "prices_daily", make_bars("JMP", closes, end=END))
    default = _report(tmp_lake)
    assert list(default["severity"]) == ["warning"] and blocked_symbols(default) == frozenset()
    strict = AtlasConfig.model_validate(
        {"data": {"quality": {"severity": {"return_outlier": "critical"}}}}
    )
    assert blocked_symbols(_report(tmp_lake, config=strict)) == frozenset({"JMP"})


def test_blocked_symbols_only_critical() -> None:
    issues = [
        QualityIssue("prices_daily", "AAA", END, "vendor_mix", W, "x", utc(2024, 2, 1)),
        QualityIssue("prices_daily", "BBB", END, "stale_price", C, "x", utc(2024, 2, 1)),
        QualityIssue("macro_observations", "DGS10", END, "macro_gap", C, "x", utc(2024, 2, 1)),
    ]
    assert blocked_symbols(issues_frame(issues)) == frozenset({"BBB", "DGS10"})
    assert blocked_symbols(issues_frame([])) == frozenset()


def test_merge_duplicate_keys() -> None:
    issues = [
        QualityIssue("prices_daily", "AAA", END, "ohlc_inconsistent", W, "first", utc(2024, 2, 1)),
        QualityIssue("prices_daily", "AAA", END, "ohlc_inconsistent", C, "second", utc(2024, 2, 2)),
        QualityIssue("prices_daily", "BBB", END, "ohlc_inconsistent", W, "other", utc(2024, 2, 1)),
    ]
    merged = merge_issues(issues_frame(issues))
    assert len(merged) == 2
    row = merged[merged["symbol"] == "AAA"].iloc[0]
    assert (row["severity"], row["detail"]) == ("critical", "first; second")
    assert row["available_at"] == pd.Timestamp(utc(2024, 2, 2))


def test_persist_replaces_day(tmp_lake: Lake) -> None:
    zero = walk(90)
    zero[-1] = 0.0
    _write(tmp_lake, "prices_daily", make_bars("BAD", zero, end=END))
    _report(tmp_lake, persist=True)
    _report(tmp_lake, date(2024, 3, 4), persist=True)
    stored = read_quality_issues(tmp_lake, AS_OF)
    # The zero close is a non-positive price and also a -100% return outlier.
    assert set(zip(stored["symbol"], stored["check"], strict=True)) == {
        ("BAD", "nonpositive_price"),
        ("BAD", "return_outlier"),
    }
    assert list(stored.columns) == list(ISSUE_COLUMNS)
    # The vendor corrects the bar; re-running 2024-03-01 replaces that day's issues only.
    fixed = make_bars("BAD", walk(90), end=END)
    tmp_lake.write_table("prices_daily", fixed, mode="overwrite")
    assert _report(tmp_lake, persist=True).empty
    assert read_quality_issues(tmp_lake, AS_OF).empty
    assert set(read_quality_issues(tmp_lake, date(2024, 3, 4))["symbol"]) == {"BAD"}


def test_persist_empty_report_clears_day(tmp_lake: Lake) -> None:
    _write(tmp_lake, "prices_daily", make_bars("AAA", walk(90), end=END))
    assert _report(tmp_lake, persist=True).empty
    assert tmp_lake.has_table("data_quality_issues")
    assert read_quality_issues(tmp_lake, AS_OF).empty


def test_persist_leaves_other_years_untouched(tmp_lake: Lake) -> None:
    # Phase 1 checkpoint, Tier 3: persisting one day rewrote the whole table; now only the files
    # holding that day's rows (and the year it is written to) change.
    old = QualityIssue(
        "prices_daily", "OLD", date(2023, 6, 1), "stale_price", W, "x", utc(2023, 6, 1)
    )
    tmp_lake.write_table(
        "data_quality_issues",
        issues_frame([old]).assign(report_day=date(2023, 6, 2)),
        mode="upsert",
    )
    (old_file,) = tmp_lake.table_files("data_quality_issues")
    stamp = old_file.stat().st_mtime_ns
    _write(tmp_lake, "prices_daily", make_bars("AAA", walk(90), end=END))
    _report(tmp_lake, persist=True)
    assert old_file.stat().st_mtime_ns == stamp
    assert set(read_quality_issues(tmp_lake, date(2023, 6, 2))["symbol"]) == {"OLD"}


def test_read_issues_guard(tmp_lake: Lake) -> None:
    issue = QualityIssue("prices_daily", "AAA", END, "stale_price", C, "x", utc(2024, 3, 1, 14))
    row = issues_frame([issue]).assign(report_day=AS_OF.day)
    tmp_lake.write_table("data_quality_issues", row, mode="upsert")
    with pytest.raises(LookAheadError):
        read_quality_issues(tmp_lake, AS_OF)


def test_report_empty_lake(tmp_lake: Lake) -> None:
    report = _report(tmp_lake)
    assert report.empty and list(report.columns) == list(ISSUE_COLUMNS)
    assert read_quality_issues(tmp_lake, AS_OF).empty


# --- Phase 2 checkpoint (Tier 1): only the checks that can block -------------------------------


def _mixed_lake(lake: Lake) -> None:
    """Critical price issues (BAD, STL), a warning outlier (JMP), a fundamentals jump (AAA) and
    a macro gap (CPIAUCSL): every table of the report has an issue."""
    zero = walk(90, seed=2)
    zero[-2] = 0.0
    jump = walk(90, seed=4)
    jump = jump[:87] + [c * 1.3 for c in jump[87:]]
    bars = pd.concat(
        [
            make_bars("AAA", walk(90, seed=1), end=END),
            make_bars("BAD", zero, end=END),
            make_bars("STL", walk(84, seed=3) + [50.0] * 6, end=END),
            make_bars("JMP", jump, end=END),
        ],
        ignore_index=True,
    )
    _write(lake, "prices_daily", bars)
    _write(lake, "corporate_actions", no_actions())
    _write(lake, "fundamentals_facts", quarterly_facts("AAA", "Revenues", [1e8, 1e8, 1e8, 2e9]))
    _write(
        lake,
        "macro_observations",
        macro_rows("CPIAUCSL", month_starts(date(2023, 1, 1), date(2023, 10, 1))),
    )


_SEVERITY_VARIANTS = [
    {},
    {"return_outlier": "critical"},
    {"fundamentals_jump": "critical"},
    {"macro_gap": "critical"},
    {"stale_price": "warning", "nonpositive_price": "info"},
    {"missing_sessions": "critical", "vendor_mix": "critical"},
]


@pytest.mark.parametrize("severity", _SEVERITY_VARIANTS)
def test_blocking_only_is_the_critical_part_of_the_full_report(
    tmp_lake: Lake, severity: dict[str, str]
) -> None:
    _mixed_lake(tmp_lake)
    cfg = AtlasConfig.model_validate({"data": {"quality": {"severity": severity}}})
    full = _report(tmp_lake, config=cfg)
    critical = full[full["severity"] == "critical"].reset_index(drop=True)
    only = _report(tmp_lake, config=cfg, blocking_only=True)
    pd.testing.assert_frame_equal(only, critical)
    assert blocked_symbols(only) == blocked_symbols(full)


def test_blocking_only_default_blocks_the_price_names(tmp_lake: Lake) -> None:
    _mixed_lake(tmp_lake)
    only = _report(tmp_lake, blocking_only=True)
    assert blocked_symbols(only) == frozenset({"BAD", "STL"})
    assert set(only["severity"]) == {"critical"}


def test_blocking_only_skips_checks_that_cannot_block(
    tmp_lake: Lake, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the default severities the fundamentals and macro checks are warnings: the
    blocking report must not run them (they are 97% of a report's time on the real lake)."""
    _mixed_lake(tmp_lake)

    def boom(*args: object, **kwargs: object) -> list[QualityIssue]:
        raise AssertionError("a check that cannot block was run")

    for name in (
        "check_fundamentals_jumps",
        "check_fundamentals_period_after_filing",
        "check_macro_gaps",
        "check_missing_sessions",
        "check_vendor_mix",
    ):
        monkeypatch.setattr(f"atlas.data.quality.{name}", boom)
    assert blocked_symbols(_report(tmp_lake, blocking_only=True)) == frozenset({"BAD", "STL"})
    with pytest.raises(AssertionError, match="cannot block"):
        _report(tmp_lake)


def test_blocking_only_runs_a_check_made_critical(tmp_lake: Lake) -> None:
    _mixed_lake(tmp_lake)
    cfg = AtlasConfig.model_validate(
        {"data": {"quality": {"severity": {"fundamentals_jump": "critical"}}}}
    )
    assert "AAA" in blocked_symbols(_report(tmp_lake, config=cfg, blocking_only=True))


def test_blocking_only_does_not_read_tables_it_does_not_check(
    tmp_lake: Lake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mixed_lake(tmp_lake)
    read: list[str] = []
    original = Lake.read_table

    def spy(self: Lake, table: str, *args: object, **kwargs: object) -> pd.DataFrame:
        read.append(table)
        return original(self, table, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Lake, "read_table", spy)
    _report(tmp_lake, blocking_only=True)
    assert set(read) == {"prices_daily", "corporate_actions"}


def test_blocking_only_cannot_be_persisted(tmp_lake: Lake) -> None:
    _mixed_lake(tmp_lake)
    with pytest.raises(ValueError, match="blocking_only"):
        _report(tmp_lake, blocking_only=True, persist=True)
    assert not tmp_lake.has_table("data_quality_issues")
