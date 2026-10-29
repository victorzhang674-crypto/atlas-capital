"""Stage 10 Task 4: point-in-time macro queries (latest vintage known at as_of), acceptance."""

from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest
from macro_helpers import CPI, DGS10, make_rows, stored_rows

import atlas.data.macro.pit as pit_module
from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError, LookAheadError
from atlas.core.pit import AsOf
from atlas.core.storage import Lake
from atlas.data.macro import (
    MACRO_OBSERVATIONS,
    Macro,
    MacroCatalog,
    known_observations,
    macro_pit,
    macro_pit_detail,
)

LONG = timedelta(days=4000)


def at(day: date) -> AsOf:
    return AsOf.for_session(day)


def cpi_on(day: date, obs: date) -> float | None:
    frame = macro_pit(stored_rows(CPI), ["CPIAUCSL"], at(day), LONG)
    stamp = pd.Timestamp(obs)
    return float(frame.loc[stamp, "CPIAUCSL"]) if stamp in frame.index else None


def test_acceptance_cpi_march_2020_unknown_on_2020_04_05() -> None:
    frame = macro_pit(stored_rows(CPI), ["CPIAUCSL"], at(date(2020, 4, 5)), LONG)
    assert pd.Timestamp("2020-03-01") not in frame.index
    assert frame.index.max() == pd.Timestamp("2020-02-01")
    assert frame.loc[pd.Timestamp("2020-02-01"), "CPIAUCSL"] == 259.050


def test_acceptance_cpi_march_2020_first_release_on_2020_04_15() -> None:
    assert cpi_on(date(2020, 4, 15), date(2020, 3, 1)) == 257.953


def test_cpi_not_known_on_release_morning() -> None:
    assert cpi_on(date(2020, 4, 10), date(2020, 3, 1)) is None  # released 08:30, cutoff 08:00
    assert cpi_on(date(2020, 4, 13), date(2020, 3, 1)) == 257.953


def test_revision_seen_only_after_its_vintage() -> None:
    assert cpi_on(date(2021, 2, 8), date(2020, 3, 1)) == 257.953
    assert cpi_on(date(2021, 2, 9), date(2020, 3, 1)) == 257.989
    assert cpi_on(date(2020, 2, 11), date(2019, 3, 1)) == 254.148
    assert cpi_on(date(2020, 2, 12), date(2019, 3, 1)) == 254.095


def month_ends() -> list[date]:
    return [d.date() for d in pd.date_range("2019-01-31", "2021-12-31", freq="ME")]


def test_acceptance_no_returned_value_released_after_as_of() -> None:
    store = stored_rows(CPI)
    for day in month_ends():
        as_of = at(day)
        detail = macro_pit_detail(store, ["CPIAUCSL"], as_of, LONG)
        assert not detail.empty
        assert (detail["available_at"] < pd.Timestamp(as_of.cutoff)).all()
        known_then = store[store["available_at"] < pd.Timestamp(as_of.cutoff)]
        pd.testing.assert_frame_equal(
            macro_pit(store, ["CPIAUCSL"], as_of, LONG),
            macro_pit(known_then, ["CPIAUCSL"], as_of, LONG),
        )


def test_future_rows_change_nothing() -> None:
    store = stored_rows(CPI)
    future = make_rows(
        (
            "CPIAUCSL",
            date(2020, 3, 1),
            999.0,
            date(2030, 1, 2),
            datetime(2030, 1, 2, 13, 30, tzinfo=UTC),
        ),
        (
            "CPIAUCSL",
            date(2020, 5, 1),
            999.0,
            date(2020, 4, 20),
            datetime(2020, 4, 20, 12, 30, tzinfo=UTC),
        ),
    )
    as_of = at(date(2020, 4, 15))
    pd.testing.assert_frame_equal(
        macro_pit(pd.concat([store, future], ignore_index=True), ["CPIAUCSL"], as_of, LONG),
        macro_pit(store, ["CPIAUCSL"], as_of, LONG),
    )


def test_guard_raises_if_filter_bypassed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pit_module, "pit_filter", lambda df, as_of: df)
    with pytest.raises(LookAheadError):
        known_observations(stored_rows(CPI), at(date(2020, 4, 5)))


def test_latest_missing_value_hides_older_value() -> None:
    rows = make_rows(
        ("S", date(2020, 1, 1), 1.0, date(2020, 2, 1), datetime(2020, 2, 1, 14, tzinfo=UTC)),
        ("S", date(2020, 1, 1), None, date(2020, 3, 2), datetime(2020, 3, 2, 14, tzinfo=UTC)),
        ("S", date(2020, 2, 1), 2.0, date(2020, 3, 2), datetime(2020, 3, 2, 14, tzinfo=UTC)),
    )
    before = macro_pit(rows, ["S"], at(date(2020, 2, 14)), LONG)
    assert before["S"].tolist() == [1.0]
    after = macro_pit(rows, ["S"], at(date(2020, 3, 16)), LONG)
    assert after.index.tolist() == [pd.Timestamp("2020-02-01")]


def test_macro_pit_shape_and_lookback() -> None:
    frame = macro_pit(
        stored_rows(CPI), ["DGS10", "CPIAUCSL"], at(date(2020, 4, 15)), timedelta(days=90)
    )
    assert list(frame.columns) == ["DGS10", "CPIAUCSL"]
    assert frame["DGS10"].isna().all()
    assert isinstance(frame.index, pd.DatetimeIndex) and frame.index.name == "obs_date"
    assert frame.index.tolist() == [pd.Timestamp("2020-02-01"), pd.Timestamp("2020-03-01")]
    assert frame.dtypes.tolist() == ["float64", "float64"]
    empty = macro_pit(stored_rows(CPI), ["CPIAUCSL"], at(date(2018, 6, 1)), LONG)
    assert empty.empty and list(empty.columns) == ["CPIAUCSL"]
    with pytest.raises(ValueError, match="lookback"):
        macro_pit(stored_rows(CPI), ["CPIAUCSL"], at(date(2020, 4, 15)), timedelta(0))


def test_detail_columns() -> None:
    detail = macro_pit_detail(
        stored_rows(CPI), ["CPIAUCSL"], at(date(2020, 4, 15)), timedelta(days=50)
    )  # obs dates are the 1st of the month: 50 days back from 2020-04-15 reaches March only
    assert list(detail.columns) == [
        "series_id",
        "obs_date",
        "value",
        "realtime_start",
        "available_at",
    ]
    assert detail.to_dict("records") == [
        {
            "series_id": "CPIAUCSL",
            "obs_date": date(2020, 3, 1),
            "value": 257.953,
            "realtime_start": date(2020, 4, 10),
            "available_at": pd.Timestamp("2020-04-10 12:30", tz="UTC"),
        }
    ]


def reader(lake: Lake) -> Macro:
    return Macro(lake, AtlasConfig(), catalog=MacroCatalog({"CPIAUCSL": CPI, "DGS10": DGS10}))


def test_macro_reader(tmp_lake: Lake) -> None:
    tmp_lake.write_table(MACRO_OBSERVATIONS.name, stored_rows(CPI), mode="upsert")
    macro = reader(tmp_lake)
    frame = macro.pit(["CPIAUCSL", "DGS10"], date(2020, 4, 15), LONG)
    assert frame.loc[pd.Timestamp("2020-03-01"), "CPIAUCSL"] == 257.953
    assert frame["DGS10"].isna().all()
    detail = macro.pit_detail(["CPIAUCSL"], at(date(2020, 4, 5)), timedelta(days=70))
    assert detail["obs_date"].tolist() == [date(2020, 2, 1)]
    assert len(macro.observations(["CPIAUCSL"])) == len(stored_rows(CPI))


def test_macro_reader_errors(tmp_lake: Lake) -> None:
    macro = reader(tmp_lake)
    with pytest.raises(DataError, match="ingest_macro"):
        macro.pit(["CPIAUCSL"], date(2020, 4, 15), LONG)
    with pytest.raises(ValueError, match="XYZ"):
        macro.pit(["XYZ"], date(2020, 4, 15), LONG)
    with pytest.raises(ValueError, match="sequence"):
        macro.pit("CPIAUCSL", date(2020, 4, 15), LONG)
    with pytest.raises(TypeError, match="datetime"):
        macro.pit(["CPIAUCSL"], datetime(2020, 4, 15, tzinfo=UTC), LONG)


def test_data_limitations_doc_covers_macro() -> None:
    from macro_helpers import REPO_ROOT

    text = (REPO_ROOT / "docs" / "data-limitations.md").read_text(encoding="utf-8")
    section = text.split("## Macro", 1)[1].lower()
    for word in ("alfred", "vintage", "release_time", "2023-10-30", "08:00", "umcsent", "units"):
        assert word in section, word
