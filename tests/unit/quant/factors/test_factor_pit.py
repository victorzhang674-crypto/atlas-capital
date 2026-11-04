"""Stage 16 acceptance: factor panels for any date use only filings available at that date.

Three checks: (1) rows known only after the as-of (a later 10-Q, a 10-K/A restatement, a
later split, later bars) change nothing at the as-of, and do change the panel once known;
(2) a filing accepted one second before the cutoff is used, one second after is not;
(3) for several as-of dates, the panel on the full lake equals the panel on a lake that
holds only the rows known at that cutoff."""

from datetime import UTC, date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from factor_world import (
    AS_OF,
    STOCKS,
    FactorWorld,
    _fact,
    actions_frame,
    build_factor_world,
    company_facts,
    facts_frame,
    fiscal_quarters,
    world_bars,
)

from atlas.core.config import FactorsConfig
from atlas.core.pit import AsOf
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.view import DataView
from atlas.quant.factors import FactorPanel, factor_panel

CFG = FactorsConfig(min_names=5)
LATER = date(2024, 6, 3)


def _lake(root: Path) -> Lake:
    return Lake(root, registry=DEFAULT_REGISTRY.copy())


def _panel(lake: Lake, day: date | AsOf) -> FactorPanel:
    return factor_panel(DataView(lake, day), config=CFG)


def _assert_same(a: FactorPanel, b: FactorPanel) -> None:
    pd.testing.assert_frame_equal(a.raw, b.raw)
    pd.testing.assert_frame_equal(a.exposures, b.exposures)
    pd.testing.assert_frame_equal(a.filled, b.filled)
    pd.testing.assert_frame_equal(a.market, b.market)
    pd.testing.assert_series_equal(a.sectors, b.sectors)


def _add_future(lake: Lake, world_companies: tuple) -> None:  # type: ignore[type-arg]
    """Rows known only after `AS_OF`: Q1 2024 10-Qs (filed 2024-05-06), a 10-K/A restating
    AAA's Q3 2023 revenue (filed 2024-03-04), a 3:1 DDD split (ex 2024-03-05) and bars
    2024-03-01..2024-05-31."""
    q1 = fiscal_quarters(date(2024, 3, 31), 1)
    rows = []
    for c in world_companies:
        rows += company_facts(c, q1)
    start, end = fiscal_quarters(date(2023, 9, 30), 1)[0]
    rows.append(_fact("AAA", "Revenues", 1.0, end, date(2024, 3, 4), start=start, form="10-K/A"))
    lake.write_table("fundamentals_facts", facts_frame(rows), mode="upsert")
    lake.write_table(
        "corporate_actions",
        actions_frame([("DDD", date(2024, 3, 5), "split", 3.0, None)]),
        mode="upsert",
    )
    later = world_bars(STOCKS, date(2024, 5, 31), 63, seed=77)
    later = later[later["date"] > date(2024, 2, 29)]
    lake.write_table("prices_daily", later, mode="upsert")


@pytest.fixture(scope="module")
def clean(tmp_path_factory: pytest.TempPathFactory) -> FactorWorld:
    """The world as known at `AS_OF` (read-only)."""
    return build_factor_world(_lake(tmp_path_factory.mktemp("clean")))


@pytest.fixture(scope="module")
def dirty(tmp_path_factory: pytest.TempPathFactory) -> FactorWorld:
    """The same world plus rows known only after `AS_OF` (read-only)."""
    world = build_factor_world(_lake(tmp_path_factory.mktemp("dirty")))
    _add_future(world.lake, world.companies)
    return world


@pytest.fixture(scope="module")
def clean_panel(clean: FactorWorld) -> FactorPanel:
    return _panel(clean.lake, AS_OF)


def test_later_rows_change_nothing_at_the_as_of(
    clean: FactorWorld, dirty: FactorWorld, clean_panel: FactorPanel
) -> None:
    _assert_same(clean_panel, _panel(dirty.lake, AS_OF))
    # ... and they are not ignored once known (the test is not vacuous)
    late_clean, late_dirty = _panel(clean.lake, LATER), _panel(dirty.lake, LATER)
    assert not late_clean.raw.equals(late_dirty.raw)
    assert (late_clean.raw["revenue_growth"] != late_dirty.raw["revenue_growth"]).all()


def test_restatement_used_only_once_public(dirty: FactorWorld) -> None:
    # AAA's 10-K/A of 2024-03-04 (21:00 UTC) restates its Q3 2023 revenue.
    before = _panel(dirty.lake, date(2024, 3, 4))  # cutoff 08:00 New York that day
    after = _panel(dirty.lake, date(2024, 3, 5))
    assert before.raw.loc["AAA", "revenue_growth"] != after.raw.loc["AAA", "revenue_growth"]
    others = [s for s in STOCKS if s != "AAA"]
    pd.testing.assert_series_equal(
        before.raw.loc[others, "revenue_growth"], after.raw.loc[others, "revenue_growth"]
    )


def test_filing_at_the_cutoff(tmp_path: Path, clean_panel: FactorPanel) -> None:
    # Restatements of Q4 2023 revenue accepted 1 s before (BBB), at (EEE) and 1 s after (III)
    # the cutoff: only BBB's is known (knowledge is strictly before the cutoff).
    world = build_factor_world(_lake(tmp_path / "w"))
    cutoff = AsOf.for_session(AS_OF).cutoff.astimezone(UTC)
    start, end = fiscal_quarters(date(2023, 12, 31), 1)[0]
    stamps = {"BBB": -1, "EEE": 0, "III": 1}
    facts = [
        _fact(
            sym,
            "Revenues",
            5.0,
            end,
            date(2024, 2, 29),
            start=start,
            form="10-K/A",
            available_at=cutoff + timedelta(seconds=offset),
        )
        for sym, offset in stamps.items()
    ]
    world.lake.write_table("fundamentals_facts", facts_frame(facts), mode="upsert")
    now = _panel(world.lake, AS_OF)
    changed = now.raw["revenue_growth"] != clean_panel.raw["revenue_growth"]
    assert list(changed[changed].index) == ["BBB"]


def _truncate(full: Lake, dest: Lake, as_of: AsOf) -> None:
    """Copy into `dest` only the rows of `full` known before `as_of.cutoff`."""
    cutoff = pd.Timestamp(as_of.cutoff)
    for table in ("fundamentals_facts", "prices_daily", "corporate_actions"):
        rows = full.read_table(table)
        dest.write_table(table, rows[rows["available_at"] < cutoff], mode="overwrite")
    for table in ("instruments", "index_membership"):
        dest.write_table(table, full.read_table(table), mode="overwrite")


@pytest.mark.parametrize("day", [date(2023, 2, 6), date(2023, 2, 7), AS_OF])
def test_panel_equals_the_panel_of_the_truncated_lake(
    dirty: FactorWorld, tmp_path: Path, day: date
) -> None:
    # 2023-02-06/07: the Q4 2022 10-Ks are accepted 2023-02-06 21:00 UTC, between the two.
    point = AsOf.for_session(day)
    truncated = _lake(tmp_path / "cut")
    _truncate(dirty.lake, truncated, point)
    _assert_same(_panel(dirty.lake, point), _panel(truncated, point))


def test_reading_the_as_of_never_sees_later_filings(dirty: FactorWorld) -> None:
    view = DataView(dirty.lake, date(2023, 6, 1))
    p = factor_panel(view, config=CFG)
    hist = view.fundamentals_history(list(STOCKS), ["revenue"])
    assert (hist["available_at"] < pd.Timestamp(view.as_of.cutoff)).all()
    assert max(hist["period_end"]) == date(2023, 3, 31)  # Q1 2023 is filed 2023-05-05
    assert p.raw["revenue_growth"].notna().all()
