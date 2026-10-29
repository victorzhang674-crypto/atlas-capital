"""Stage 10 Task 3: parsing, real-time windows, vintage merging, release availability, ingest."""

import math
from collections.abc import Iterator
from datetime import UTC, date, datetime, time

import pandas as pd
import pytest
import respx
from macro_helpers import (
    CPI,
    CPI_REQUEST,
    FakeFred,
    fixture_json,
    fred_client,
    serve_fixture,
    window_requests,
)

from atlas.core.config import AtlasConfig, DataConfig, MacroConfig
from atlas.core.errors import DataError
from atlas.core.storage import Lake, validate_frame
from atlas.data.macro import (
    MACRO_OBSERVATIONS,
    MACRO_SERIES,
    OBS_COLUMNS,
    MacroCatalog,
    MacroSeries,
    attach_available_at,
    collapse_vintages,
    ingest_macro,
    parse_observations,
    parse_series_metadata,
    vintage_windows,
)


def run_config(macro: MacroConfig) -> AtlasConfig:
    return AtlasConfig(data=DataConfig(macro=macro))


CPI_CONFIG = run_config(
    MacroConfig(
        observation_start=CPI_REQUEST["observation_start"],
        vintage_start=CPI_REQUEST["realtime_start"],
    )
)
CPI_ENDS = {
    "observation_end": CPI_REQUEST["observation_end"],
    "realtime_end": CPI_REQUEST["realtime_end"],
}


@pytest.fixture
def fred() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.route(host="api.stlouisfed.org").mock(side_effect=serve_fixture)
        yield router


def cpi_rows() -> pd.DataFrame:
    return parse_observations(fixture_json("observations/CPIAUCSL.json"), "CPIAUCSL")


# --- parsing ------------------------------------------------------------------------------------


def test_parse_observations_cpi() -> None:
    rows = cpi_rows()
    assert list(rows.columns) == ["series_id", "obs_date", "value", "realtime_start"]
    assert len(rows) == 114
    march = rows[rows["obs_date"] == date(2020, 3, 1)]
    assert list(march["realtime_start"]) == [date(2020, 4, 10), date(2021, 2, 8)]
    assert list(march["value"]) == [257.953, 257.989]
    assert rows["value"].dtype == "float64"


def test_parse_observations_missing_is_nan() -> None:
    payload = {
        "count": 1,
        "observations": [
            {
                "realtime_start": "2020-04-14",
                "realtime_end": "9999-12-31",
                "date": "2020-04-10",
                "value": ".",
            }
        ],
    }
    rows = parse_observations(payload, "DGS10")
    assert math.isnan(rows["value"].iloc[0])


@pytest.mark.parametrize(
    ("payload", "pattern"),
    [
        ({"count": 0}, "observations"),
        (
            {
                "observations": [
                    {"realtime_start": "2020-01-01", "date": "2020-13-01", "value": "1"}
                ]
            },
            "date",
        ),
        (
            {
                "observations": [
                    {"realtime_start": "2020-01-01", "date": "2020-01-01", "value": "abc"}
                ]
            },
            "value",
        ),
        ({"observations": [{"date": "2020-01-01", "value": "1"}]}, "realtime_start"),
    ],
)
def test_parse_observations_malformed_raises(payload: dict[str, object], pattern: str) -> None:
    with pytest.raises(DataError, match=pattern):
        parse_observations(payload, "X")


def test_parse_series_metadata() -> None:
    meta = parse_series_metadata(fixture_json("series/CPIAUCSL.json"), CPI)
    row = meta.iloc[0].to_dict()
    assert row == {
        "series_id": "CPIAUCSL",
        "title": "Consumer Price Index for All Urban Consumers: All Items in U.S. City Average",
        "frequency": "M",
        "units": "Index 1982-1984=100",
        "seasonal_adjustment": "SA",
        "kind": "release",
    }
    validate_frame(MACRO_SERIES, meta)
    with pytest.raises(DataError, match="DGS10"):
        parse_series_metadata(fixture_json("series/DGS10.json"), CPI)
    with pytest.raises(DataError, match="seriess"):
        parse_series_metadata({"seriess": []}, CPI)


# --- real-time windows and vintage merging --------------------------------------------------------


def test_vintage_windows() -> None:
    start, end = date(2000, 1, 1), date(9999, 12, 31)
    d = [date(2020, 1, k) for k in (5, 10, 15, 20, 25)]
    assert vintage_windows(d, start, end, 3) == [
        (start, date(2020, 1, 14)),
        (d[2], date(2020, 1, 24)),
        (d[4], end),
    ]
    assert vintage_windows([], start, end, 3) == [(start, end)]
    assert vintage_windows(d, start, end, 2000) == [(start, end)]
    assert len(vintage_windows(d, start, end, 2)) == 5  # one vintage per window
    with pytest.raises(ValueError, match="max_dates"):
        vintage_windows(d, start, end, 1)


def test_collapse_vintages_merges_equal_values() -> None:
    nan = float("nan")
    rows = pd.DataFrame(
        {
            "series_id": ["S"] * 5,
            "obs_date": [date(2020, 1, 1)] * 4 + [date(2020, 2, 1)],
            "value": [1.0, 1.0, 2.0, nan, nan],
            "realtime_start": [
                date(2020, 2, 1),
                date(2020, 3, 1),
                date(2020, 4, 1),
                date(2020, 5, 1),
                date(2020, 3, 1),
            ],
        }
    )
    extra = rows.iloc[[3]].assign(realtime_start=date(2020, 6, 1))  # NaN again: merged
    out = collapse_vintages(pd.concat([rows, extra]).sample(frac=1, random_state=0))
    assert list(out["realtime_start"]) == [
        date(2020, 2, 1),
        date(2020, 4, 1),
        date(2020, 5, 1),
        date(2020, 3, 1),
    ]
    assert out["value"].iloc[1] == 2.0


# --- availability (release series) ----------------------------------------------------------------


def test_release_available_at_is_release_time_et() -> None:
    out = attach_available_at(cpi_rows(), CPI, MacroConfig())
    assert list(out.columns) == list(OBS_COLUMNS)
    first = out[
        (out["obs_date"] == date(2020, 3, 1)) & (out["realtime_start"] == date(2020, 4, 10))
    ]
    assert first["available_at"].iloc[0] == pd.Timestamp("2020-04-10 12:30", tz="UTC")  # 08:30 EDT
    jan = out[(out["obs_date"] == date(2020, 1, 1)) & (out["realtime_start"] == date(2020, 2, 13))]
    assert jan["available_at"].iloc[0] == pd.Timestamp("2020-02-13 13:30", tz="UTC")  # 08:30 EST
    assert str(out["available_at"].dtype) == "datetime64[us, UTC]"
    validate_frame(MACRO_OBSERVATIONS, out)


# --- ingest ---------------------------------------------------------------------------------------


def catalog(*series: MacroSeries) -> MacroCatalog:
    return MacroCatalog({s.id: s for s in series})


def test_ingest_cpi_fixture(fred: respx.MockRouter, tmp_lake: Lake) -> None:
    with fred_client(tmp_lake) as client:
        report = ingest_macro(
            tmp_lake, client, ["CPIAUCSL"], config=CPI_CONFIG, catalog=catalog(CPI), **CPI_ENDS
        )
    assert report.series_ok == ("CPIAUCSL",)
    assert report.series_failed == ()
    assert report.rows == 114
    assert report.revision_rows == 114 - 42
    stored = tmp_lake.read_table(MACRO_OBSERVATIONS.name)
    assert len(stored) == 114
    assert set(stored["series_id"]) == {"CPIAUCSL"}
    assert tmp_lake.read_table(MACRO_SERIES.name)["frequency"].tolist() == ["M"]
    windows = [c.request.url.params for c in fred.calls if "output_type" in c.request.url.params]
    assert len(windows) == 1


def test_ingest_is_idempotent(fred: respx.MockRouter, tmp_lake: Lake) -> None:
    for _ in range(2):
        ingest_macro(
            tmp_lake,
            fred_client(),
            ["CPIAUCSL"],
            config=CPI_CONFIG,
            catalog=catalog(CPI),
            **CPI_ENDS,
        )
    assert len(tmp_lake.read_table(MACRO_OBSERVATIONS.name)) == 114


FAKE = MacroSeries(id="FAKE", kind="release", release_time=time(8, 30), description="fake")
VINTAGES = {
    date(2020, 2, 10): {date(2020, 1, 1): 1.0},
    date(2020, 3, 10): {date(2020, 1, 1): 1.0, date(2020, 2, 1): 2.0},
    date(2020, 4, 10): {date(2020, 1, 1): 1.5, date(2020, 2, 1): 2.0, date(2020, 3, 1): 3.0},
    date(2020, 5, 10): {date(2020, 1, 1): 1.5, date(2020, 2, 1): None, date(2020, 3, 1): 3.0},
    date(2020, 6, 10): {
        date(2020, 1, 1): 1.5,
        date(2020, 2, 1): None,
        date(2020, 3, 1): 3.0,
        date(2020, 4, 1): 4.0,
    },
    date(2020, 7, 10): {
        date(2020, 1, 1): 1.25,
        date(2020, 2, 1): 2.5,
        date(2020, 3, 1): 3.0,
        date(2020, 4, 1): 4.0,
    },
    date(2020, 8, 10): {
        date(2020, 1, 1): 1.25,
        date(2020, 2, 1): 2.5,
        date(2020, 3, 1): 3.0,
        date(2020, 4, 1): 4.0,
    },
}


def run_fake(lake: Lake, max_vintages: int) -> FakeFred:
    fake = FakeFred("FAKE", VINTAGES, max_vintages=max_vintages)
    config = MacroConfig(vintage_start=date(2020, 1, 1), max_vintage_dates_per_request=max_vintages)
    with respx.mock() as router:
        router.route(host="api.stlouisfed.org").mock(side_effect=fake)
        report = ingest_macro(
            lake,
            fred_client(config=config),
            ["FAKE"],
            config=run_config(config),
            catalog=catalog(FAKE),
        )
    assert report.series_ok == ("FAKE",)
    return fake


def test_windowed_ingest_equals_single_request(tmp_path) -> None:  # type: ignore[no-untyped-def]
    single, split = Lake(tmp_path / "a"), Lake(tmp_path / "b")
    one = run_fake(single, 2000)
    three = run_fake(split, 3)
    assert len(window_requests(one)) == 1
    assert window_requests(three) == [
        ("2020-01-01", "2020-04-09"),
        ("2020-04-10", "2020-06-09"),
        ("2020-06-10", "2020-08-09"),
        ("2020-08-10", "9999-12-31"),
    ]
    a = single.read_table(MACRO_OBSERVATIONS.name)
    b = split.read_table(MACRO_OBSERVATIONS.name)
    pd.testing.assert_frame_equal(a, b)
    jan = a[a["obs_date"] == date(2020, 1, 1)]
    assert list(jan["value"]) == [1.0, 1.5, 1.25]
    assert list(jan["realtime_start"]) == [date(2020, 2, 10), date(2020, 4, 10), date(2020, 7, 10)]
    feb = a[a["obs_date"] == date(2020, 2, 1)]
    assert feb["value"].isna().tolist() == [False, True, False]
    assert len(a) == 8  # Jan 3, Feb 3, Mar 1, Apr 1


def test_ingest_failing_series_does_not_abort(fred: respx.MockRouter, tmp_lake: Lake) -> None:
    nosuch = MacroSeries(id="NOSUCH", kind="market", description="missing")
    report = ingest_macro(
        tmp_lake,
        fred_client(),
        ["NOSUCH", "CPIAUCSL"],
        config=CPI_CONFIG,
        catalog=catalog(nosuch, CPI),
        **CPI_ENDS,
    )
    assert report.series_ok == ("CPIAUCSL",)
    assert report.series_failed == ("NOSUCH",)
    assert "does not exist" in report.failure_reasons["NOSUCH"]


def test_ingest_validates_request(tmp_lake: Lake) -> None:
    client = fred_client()
    with pytest.raises(ValueError, match="sequence"):
        ingest_macro(tmp_lake, client, "CPIAUCSL", catalog=catalog(CPI))
    with pytest.raises(ValueError, match="XYZ"):
        ingest_macro(tmp_lake, client, ["CPIAUCSL", "XYZ"], catalog=catalog(CPI))


def test_ingest_defaults_to_whole_catalog(fred: respx.MockRouter, tmp_lake: Lake) -> None:
    report = ingest_macro(
        tmp_lake, fred_client(), None, config=CPI_CONFIG, catalog=catalog(CPI), **CPI_ENDS
    )
    assert report.series_ok == ("CPIAUCSL",)
    assert datetime(2020, 4, 10, 12, 30, tzinfo=UTC) in set(
        tmp_lake.read_table(MACRO_OBSERVATIONS.name)["available_at"]
    )
