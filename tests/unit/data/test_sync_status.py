"""Stage 14 Task 6: `lake_status` (rows, ranges, last sync, coverage, quality) on a synthetic
lake with known answers."""

import math
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest
from view_helpers import macro_rows, make_facts

from atlas.core.calendar import TradingCalendar
from atlas.core.clock import SimClock, use_clock
from atlas.core.config import AtlasConfig
from atlas.core.pit import bar_available_at
from atlas.core.storage import Lake
from atlas.data.macro import load_macro_series
from atlas.data.quality import DATA_QUALITY_ISSUES, ISSUE_COLUMNS
from atlas.data.sync import CoverageLine, LakeStatus, SyncState, lake_status, state_path

TODAY = date(2024, 11, 1)
CAL = TradingCalendar()


def bars(symbol: str, first: date, last: date) -> pd.DataFrame:
    days = [CAL.next_session(first) if not CAL.is_session(first) else first, last]
    frame = pd.DataFrame(
        {
            "symbol": symbol,
            "date": days,
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "adj_close": 10.0,
            "volume": 100,
            "vendor": "fake",
            "available_at": [bar_available_at(d, CAL) for d in days],
        }
    )
    frame["available_at"] = pd.to_datetime(frame["available_at"], utc=True).astype(
        "datetime64[us, UTC]"
    )
    return frame


def membership(*symbols: str) -> pd.DataFrame:
    rows = [("SP500", s, date(2010, 1, 4), None) for s in symbols]
    rows.append(("SP500", "GONE", date(2010, 1, 4), date(2020, 1, 2)))
    frame = pd.DataFrame(rows, columns=["index_id", "symbol", "start_date", "end_date"])
    frame["end_date"] = frame["end_date"].astype(object).where(frame["end_date"].notna(), None)
    return frame


def build(lake: Lake, cfg: AtlasConfig) -> None:
    lake.write_table("index_membership", membership("AAA", "BBB", "CCC", "DDD"))
    last = date(2024, 10, 31)
    lake.write_table(
        "prices_daily",
        pd.concat(
            [
                bars("AAA", date(2015, 10, 30), last),  # 9.0 years
                bars("BBB", date(2016, 10, 31), last),  # exactly 8.0 years
                bars("CCC", date(2016, 12, 12), last),  # 7.9 years
                bars("GONE", date(2012, 1, 3), date(2019, 12, 31)),  # not a current member
                bars("SPY", date(2010, 1, 4), last),
            ],
            ignore_index=True,
        ),
        mode="upsert",
    )
    facts = pd.concat(
        [
            make_facts(s, {"tag": "Assets", "value": 1e9, "end": date(2024, 6, 30),
                           "filed": date(2024, 8, 1)})
            for s in ("AAA", "BBB", "GONE")
        ],
        ignore_index=True,
    )  # fmt: skip
    lake.write_table("fundamentals_facts", facts, mode="upsert")
    catalog = load_macro_series(cfg.data.macro.series_file)
    lake.write_table(
        "macro_observations",
        pd.concat(
            [macro_rows(sid, [date(2024, 9, 3)]) for sid in catalog.ids[:-1]], ignore_index=True
        ),
        mode="upsert",
    )
    issues = pd.DataFrame(
        [
            ("prices_daily", "CCC", date(2024, 10, 30), "stale_price", "critical", "flat"),
            ("prices_daily", "AAA", date(2024, 10, 29), "return_outlier", "warning", "jump"),
            ("prices_daily", "BBB", date(2024, 10, 29), "missing_sessions", "warning", "gap"),
        ],
        columns=["table", "symbol", "date", "check", "severity", "detail"],
    )
    issues.insert(0, "report_day", date(2024, 11, 1))
    issues["available_at"] = pd.Timestamp(datetime(2024, 10, 31, 21, tzinfo=UTC)).as_unit("us")
    lake.write_table(DATA_QUALITY_ISSUES.name, issues[["report_day", *ISSUE_COLUMNS]])
    state = SyncState(state_path(lake, cfg))
    state.record(
        "prices", ["AAA"], date(2015, 1, 1), last, synced_at=datetime(2024, 11, 1, 1, tzinfo=UTC)
    )
    state.record(
        "macro", ["DGS10"], date(2015, 1, 1), last, synced_at=datetime(2024, 11, 1, 2, tzinfo=UTC)
    )
    state.add_run({"command": "sync", "run_id": "r1", "errors": [], "crashed": None})
    state.save()


@pytest.fixture
def cfg() -> AtlasConfig:
    return AtlasConfig()


@pytest.fixture
def status(tmp_lake: Lake, cfg: AtlasConfig) -> LakeStatus:
    build(tmp_lake, cfg)
    with use_clock(SimClock(start=TODAY)):
        return lake_status(tmp_lake, cfg)


def line(status: LakeStatus, dataset: str) -> CoverageLine:
    return next(c for c in status.coverage if c.dataset == dataset)


def test_table_summary_rows_keys_dates(status: LakeStatus) -> None:
    t = status.tables.set_index("table")
    assert t.loc["prices_daily", "rows"] == 10
    assert t.loc["prices_daily", "keys"] == 5
    assert t.loc["prices_daily", "first"] == date(2010, 1, 4)
    assert t.loc["prices_daily", "last"] == date(2024, 10, 31)
    assert t.loc["fundamentals_facts", "keys"] == 3
    assert t.loc["news", "rows"] == 0 and pd.isna(t.loc["news", "first"])
    assert t.loc["index_membership", "dataset"] == "universe"
    assert list(status.tables.columns) == [
        "table", "dataset", "rows", "keys", "first", "last", "last_sync"
    ]  # fmt: skip


def test_last_sync_per_dataset(status: LakeStatus) -> None:
    t = status.tables.set_index("table")
    assert t.loc["prices_daily", "last_sync"] == datetime(2024, 11, 1, 1, tzinfo=UTC)
    assert t.loc["corporate_actions", "last_sync"] == datetime(2024, 11, 1, 1, tzinfo=UTC)
    assert t.loc["macro_observations", "last_sync"] == datetime(2024, 11, 1, 2, tzinfo=UTC)
    assert pd.isna(t.loc["news", "last_sync"])
    assert status.last_run is not None and status.last_run["run_id"] == "r1"


def test_price_coverage_counts_eight_years(status: LakeStatus) -> None:
    prices = line(status, "prices")
    assert (prices.covered, prices.total) == (2, 4)  # AAA and BBB; CCC 7.9 years; DDD none
    assert prices.pct == 50.0
    assert "8" in prices.rule
    assert status.members == 4


@pytest.mark.parametrize(
    ("ccc_from", "total", "missing"),
    [
        (date(2015, 1, 1), 3, ("DDD",)),  # vendor asked from 2015: CCC's history is short
        (date(2016, 12, 1), 4, ("CCC", "DDD")),  # never asked 8 years back: a real gap
    ],
)
def test_price_coverage_of_members_listed_eight_years(
    tmp_lake: Lake, cfg: AtlasConfig, ccc_from: date, total: int, missing: tuple[str, ...]
) -> None:
    # Phase 1 checkpoint, Tier 2: the owner-approved M1 reading (Decisions log 2026-11-01)
    # counts members whose vendor history is at least 8 years long. A member is left out of
    # it only when the sync asked the vendor for more than 8 years and the first bar came
    # later; no bars at all, or a window never asked for, stays missing.
    build(tmp_lake, cfg)
    state = SyncState.load(state_path(tmp_lake, cfg))
    state.record(
        "prices", ["CCC"], ccc_from, date(2024, 10, 31), synced_at=datetime(2024, 11, 1, tzinfo=UTC)
    )
    state.save()
    with use_clock(SimClock(start=TODAY)):
        result = lake_status(tmp_lake, cfg)
    raw, listed = result.coverage[0], result.coverage[1]
    assert (raw.dataset, raw.covered, raw.total) == ("prices", 2, 4)
    assert listed.dataset == "prices (listed >= 8y)"
    assert (listed.covered, listed.total, listed.missing) == (2, total, missing)
    assert [c.dataset for c in result.coverage[2:]] == ["fundamentals", "macro", "news", "events"]


def test_fundamentals_and_macro_coverage(status: LakeStatus, cfg: AtlasConfig) -> None:
    fundamentals = line(status, "fundamentals")
    assert (fundamentals.covered, fundamentals.total) == (2, 4)
    macro = line(status, "macro")
    n = len(load_macro_series(cfg.data.macro.series_file).ids)
    assert (macro.covered, macro.total) == (n - 1, n)
    assert macro.missing == (load_macro_series(cfg.data.macro.series_file).ids[-1],)
    assert line(status, "news").covered == 0


def test_quality_summary_latest_report(status: LakeStatus) -> None:
    assert status.quality_day == date(2024, 11, 1)
    assert dict(zip(status.quality["severity"], status.quality["issues"], strict=True)) == {
        "critical": 1,
        "warning": 2,
    }
    assert status.blocked == ("CCC",)


def test_status_on_empty_lake(tmp_lake: Lake, cfg: AtlasConfig) -> None:
    with use_clock(SimClock(start=TODAY)):
        status = lake_status(tmp_lake, cfg)
    assert status.tables["rows"].sum() == 0
    assert status.members == 0
    assert status.quality_day is None and status.blocked == ()
    prices = line(status, "prices")
    assert prices.total == 0 and math.isnan(prices.pct)
    assert status.note is not None and "universe" in status.note
    assert status.last_run is None


def test_status_writes_nothing(tmp_lake: Lake, cfg: AtlasConfig) -> None:
    build(tmp_lake, cfg)

    def listing(root: Path) -> list[tuple[str, int]]:
        return sorted((str(p.relative_to(root)), p.stat().st_mtime_ns) for p in root.rglob("*"))

    before = listing(tmp_lake.root)
    with use_clock(SimClock(start=TODAY)):
        lake_status(tmp_lake, cfg)
    assert listing(tmp_lake.root) == before
