"""Phase 1 checkpoint: `DataView` reuses each symbol's concept values while its known facts
are unchanged (one panel of 500 names cost ~100 s per as-of day on the real lake).

Every cached answer must equal the uncached reader's (`Fundamentals.pit`) on the same day,
including days where only the staleness floor moved."""

from datetime import date

import pandas as pd
import pytest
from view_helpers import make_facts, quarterly_facts, repo_concept_map

import atlas.data.view as view_module
from atlas.core.config import AtlasConfig
from atlas.core.storage import Lake
from atlas.data.fundamentals import Fundamentals, concept_values
from atlas.data.view import DataView

REVENUE = (100e6, 105e6, 110e6, 115e6)  # filed 2023-05-05, 08-04, 11-03, 2024-02-15
DAYS = (
    date(2023, 12, 1),  # three quarters known
    date(2023, 12, 15),  # same facts known: reused
    date(2024, 2, 20),  # the 10-K is public: new facts
    date(2024, 3, 1),  # same facts again
    date(2024, 6, 3),  # the restatement (filed 2024-05-01) is public
    date(2025, 3, 3),  # same facts, but past the 400-day staleness floor for the old balance
)


def _facts() -> pd.DataFrame:
    return pd.concat(
        [
            quarterly_facts("AAA", "Revenues", REVENUE),
            quarterly_facts("AAA", "Assets", (1e9, 1.1e9, 1.2e9, 1.3e9), instant=True),
            quarterly_facts("BBB", "Revenues", (50e6, 51e6, 52e6, 53e6)),
            make_facts(  # AAA restates Q4 revenue in a 10-K/A
                "AAA",
                {"tag": "Revenues", "value": 90e6, "start": date(2023, 10, 1),
                 "end": date(2023, 12, 31), "filed": date(2024, 5, 1), "form": "10-K/A"},
            ),
        ],
        ignore_index=True,
    )  # fmt: skip


@pytest.fixture
def counted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Records one entry per `concept_values` call made by `DataView`."""
    calls: list[str] = []

    def counting(*args: object, **kwargs: object) -> object:
        calls.append("call")
        return concept_values(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(view_module, "concept_values", counting)
    return calls


def test_cached_answers_equal_the_reader(tmp_lake: Lake, counted: list[str]) -> None:
    tmp_lake.write_table("fundamentals_facts", _facts(), mode="upsert")
    cmap = repo_concept_map()
    reader = Fundamentals(tmp_lake, concept_map=cmap)
    view = DataView(tmp_lake, DAYS[0], concept_map=cmap)
    for day in DAYS:
        view = view.advance(day)
        for symbol in ("AAA", "BBB"):
            pd.testing.assert_series_equal(view.fundamentals(symbol), reader.pit(symbol, day))
        panel = view.fundamentals_panel(["AAA", "BBB"])
        for symbol in ("AAA", "BBB"):
            pd.testing.assert_series_equal(
                panel.loc[symbol], reader.pit(symbol, day), check_names=False
            )
    # AAA: 4 distinct known sets (Dec, Feb, Jun; Mar-2025 = Jun); BBB: Dec and Feb only.
    assert len(counted) == 3 + 2
    late = view.fundamentals("AAA")
    assert pd.isna(late["total_assets"])  # stale on 2025-03-03 though computed for 2024-06-03
    assert DataView(tmp_lake, DAYS[4], concept_map=cmap).fundamentals("AAA")[
        "revenue_ttm"
    ] == pytest.approx(100e6 + 105e6 + 110e6 + 90e6)


def test_a_lake_write_is_seen(tmp_lake: Lake, counted: list[str]) -> None:
    tmp_lake.write_table("fundamentals_facts", _facts(), mode="upsert")
    cmap = repo_concept_map()
    view = DataView(tmp_lake, DAYS[2], concept_map=cmap)
    before = view.fundamentals("BBB")["revenue_ttm"]
    fixed = quarterly_facts("BBB", "Revenues", (50e6, 51e6, 52e6, 63e6))  # same keys, new value
    tmp_lake.write_table("fundamentals_facts", fixed, mode="upsert")
    after = view.advance(DAYS[3]).fundamentals("BBB")["revenue_ttm"]
    assert (before, after) == (pytest.approx(206e6), pytest.approx(216e6))
    assert len(counted) == 2


def test_cache_size_is_configured(tmp_lake: Lake, counted: list[str]) -> None:
    tmp_lake.write_table("fundamentals_facts", _facts(), mode="upsert")
    cfg = AtlasConfig.model_validate({"data": {"view": {"fundamentals_cache_size": 1}}})
    view = DataView(tmp_lake, DAYS[3], cfg, concept_map=repo_concept_map())
    view.fundamentals_panel(["AAA", "BBB"])  # BBB's values evict AAA's
    view.advance(date(2024, 3, 4)).fundamentals_panel(["AAA", "BBB"])
    assert len(counted) == 4
