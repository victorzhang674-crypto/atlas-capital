"""Stage 16 Task 3 (Stage 13 code): `DataView.fundamentals_history` (every known fiscal period
of a concept) and `DataView.split_factors` (the split factor of each session), the inputs of
the fundamental factors' growth, stability, issuance and market-cap calculations."""

from datetime import date

import numpy as np
import pandas as pd
import pytest
from view_helpers import (
    make_actions,
    make_bars,
    make_facts,
    no_actions,
    quarterly_facts,
    repo_concept_map,
    sessions_ending,
    walk,
)

import atlas.data.view as view_module
from atlas.core.errors import DataError
from atlas.core.storage import Lake
from atlas.data.fundamentals import HISTORY_COLUMNS, concept_history
from atlas.data.view import DataView

REVENUE = (100e6, 105e6, 110e6, 115e6)  # filed 2023-05-05, 08-04, 11-03, 2024-02-15


def _facts() -> pd.DataFrame:
    return pd.concat(
        [
            quarterly_facts("AAA", "Revenues", REVENUE),
            quarterly_facts("AAA", "Assets", (1e9, 1.1e9, 1.2e9, 1.3e9), instant=True),
            quarterly_facts("BBB", "Revenues", (50e6, 51e6, 52e6, 53e6)),
        ],
        ignore_index=True,
    )


def _view(lake: Lake, day: date) -> DataView:
    return DataView(lake, day, concept_map=repo_concept_map())


# --- fundamentals_history --------------------------------------------------------------------


def test_history_rows_and_columns(tmp_lake: Lake) -> None:
    tmp_lake.write_table("fundamentals_facts", _facts(), mode="upsert")
    h = _view(tmp_lake, date(2024, 3, 1)).fundamentals_history(
        ["BBB", "AAA", "CCC"], ["revenue", "total_assets"]
    )
    assert list(h.columns) == ["symbol", *HISTORY_COLUMNS]
    assert list(h["symbol"].drop_duplicates()) == ["BBB", "AAA"]  # request order; CCC has none
    aaa_rev = h[(h["symbol"] == "AAA") & (h["concept"] == "revenue")]
    assert list(aaa_rev["value"]) == list(REVENUE)
    assert aaa_rev["ttm"].iloc[-1] == pytest.approx(sum(REVENUE))
    assert list(h[(h["symbol"] == "AAA") & (h["concept"] == "total_assets")]["value"]) == [
        1e9, 1.1e9, 1.2e9, 1.3e9
    ]  # fmt: skip
    assert set(h[h["symbol"] == "BBB"]["concept"]) == {"revenue"}


def test_history_matches_concept_history(tmp_lake: Lake) -> None:
    from atlas.core.config import FundamentalsConfig
    from atlas.core.pit import AsOf
    from atlas.data.fundamentals import known_facts

    facts = _facts()
    tmp_lake.write_table("fundamentals_facts", facts, mode="upsert")
    day = date(2023, 12, 1)
    got = _view(tmp_lake, day).fundamentals_history(["AAA"], ["revenue", "total_assets"])
    own = facts[facts["symbol"] == "AAA"]
    want = concept_history(
        known_facts(own, AsOf.for_session(day)),
        repo_concept_map(),
        ("revenue", "total_assets"),
        FundamentalsConfig(),
    )
    pd.testing.assert_frame_equal(
        got.drop(columns="symbol").reset_index(drop=True), want, check_dtype=False
    )
    assert len(got[got["concept"] == "revenue"]) == 3  # the 10-K of 2024-02-15 is not known


def test_history_later_filing_invisible(tmp_lake: Lake) -> None:
    late = make_facts(
        "AAA",
        {"tag": "Revenues", "value": 90e6, "start": date(2023, 10, 1), "end": date(2023, 12, 31),
         "filed": date(2024, 5, 1), "form": "10-K/A"},
    )  # fmt: skip
    tmp_lake.write_table("fundamentals_facts", pd.concat([_facts(), late]), mode="upsert")
    before = _view(tmp_lake, date(2024, 3, 1)).fundamentals_history(["AAA"], ["revenue"])
    after = _view(tmp_lake, date(2024, 6, 3)).fundamentals_history(["AAA"], ["revenue"])
    assert before["value"].iloc[-1] == 115e6
    assert after["value"].iloc[-1] == 90e6
    assert (before["available_at"] < pd.Timestamp("2024-03-01 13:00", tz="UTC")).all()


def test_history_validation(tmp_lake: Lake) -> None:
    view = _view(tmp_lake, date(2024, 3, 1))
    with pytest.raises(DataError, match="fundamentals_facts is empty"):
        view.fundamentals_history(["AAA"], ["revenue"])
    tmp_lake.write_table("fundamentals_facts", _facts(), mode="upsert")
    view = _view(tmp_lake, date(2024, 3, 1))
    with pytest.raises(ValueError, match="unknown concept"):
        view.fundamentals_history(["AAA"], ["nope"])
    with pytest.raises(ValueError, match="concepts must be a sequence"):
        view.fundamentals_history(["AAA"], "revenue")


def test_history_empty_result(tmp_lake: Lake) -> None:
    tmp_lake.write_table("fundamentals_facts", _facts(), mode="upsert")
    h = _view(tmp_lake, date(2024, 3, 1)).fundamentals_history(["ZZZ"], ["revenue"])
    assert h.empty
    assert list(h.columns) == ["symbol", *HISTORY_COLUMNS]


def test_history_reuses_per_symbol_cache(tmp_lake: Lake, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def counting(*args: object, **kwargs: object) -> object:
        calls.append("x")
        return concept_history(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(view_module, "concept_history", counting)
    tmp_lake.write_table("fundamentals_facts", _facts(), mode="upsert")
    view = _view(tmp_lake, date(2023, 12, 1))
    view.fundamentals_history(["AAA", "BBB"], ["revenue"])
    view.advance(date(2023, 12, 15)).fundamentals_history(["AAA", "BBB"], ["revenue"])
    assert len(calls) == 2  # same known facts on 12-15: reused
    view.advance(date(2024, 2, 20)).fundamentals_history(["AAA", "BBB"], ["revenue"])
    assert len(calls) == 4  # the 10-Ks of 2024-02-15 are public


# --- split_factors ---------------------------------------------------------------------------

END = date(2024, 2, 29)
SPLIT_DAY = date(2024, 1, 16)


def _split_lake(lake: Lake) -> list[date]:
    days = sessions_ending(END, 40)
    at = days.index(SPLIT_DAY)
    walked = walk(40, seed=3)
    closes = walked[:at] + [c / 2 for c in walked[at:]]
    bars = pd.concat([make_bars("AAA", closes, end=END), make_bars("BBB", walk(40), end=END)])
    lake.write_table("prices_daily", bars, mode="upsert")
    lake.write_table("corporate_actions", make_actions(("AAA", SPLIT_DAY, "split", 2.0, None)),
                     mode="upsert")  # fmt: skip
    return days


def test_split_factors_around_a_split(tmp_lake: Lake) -> None:
    days = _split_lake(tmp_lake)
    f = _view(tmp_lake, date(2024, 3, 1)).split_factors(["AAA", "BBB"], 40)
    assert list(f.columns) == ["AAA", "BBB"]
    assert list(f.index) == list(pd.to_datetime(days))
    assert f.index.name == "date"
    at = days.index(SPLIT_DAY)
    assert np.allclose(f["AAA"].iloc[:at], 0.5)
    assert np.allclose(f["AAA"].iloc[at:], 1.0)
    assert np.allclose(f["BBB"], 1.0)
    assert f.dtypes.eq("float64").all()


def test_split_factors_later_split_invisible(tmp_lake: Lake) -> None:
    _split_lake(tmp_lake)
    f = _view(tmp_lake, date(2024, 1, 12)).split_factors(["AAA"], 20)
    assert np.allclose(f["AAA"], 1.0)  # the split goes ex on 01-16, after the as-of


def test_split_factors_without_actions_table(tmp_lake: Lake) -> None:
    tmp_lake.write_table("prices_daily", make_bars("BBB", walk(10), end=END), mode="upsert")
    tmp_lake.write_table("corporate_actions", no_actions(), mode="upsert")
    f = _view(tmp_lake, date(2024, 3, 1)).split_factors(["BBB", "CCC"], 10)
    assert list(f.columns) == ["BBB", "CCC"]
    assert np.allclose(f.to_numpy(), 1.0)


def test_split_factors_lookback_validation(tmp_lake: Lake) -> None:
    _split_lake(tmp_lake)
    with pytest.raises(ValueError, match="lookback"):
        _view(tmp_lake, date(2024, 3, 1)).split_factors(["AAA"], 0)
