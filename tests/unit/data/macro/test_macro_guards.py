"""Stage 10: error paths of the macro package (malformed FRED answers, bad catalog entries)."""

from datetime import UTC, date, time

import httpx
import pytest
import respx
from macro_helpers import API, CPI, CPI_REQUEST, fred_client
from pydantic import ValidationError

from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.storage import Lake
from atlas.data.macro import (
    MACRO_SERIES,
    Macro,
    MacroCatalog,
    MacroSeries,
    parse_observations,
    parse_series_metadata,
)

VINTAGES_URL = f"{API}/series/vintagedates"
WINDOW = {"realtime_start": date(2020, 1, 1), "realtime_end": date(2020, 12, 31)}


@pytest.mark.parametrize(
    ("body", "pattern"),
    [
        ({"count": 1}, "no vintage_dates"),
        ({"count": 1, "vintage_dates": ["2020-13-01"]}, "bad date"),
        ({"count": 2, "vintage_dates": []}, "got 0 of 2"),
    ],
)
def test_vintage_dates_malformed(body: dict[str, object], pattern: str) -> None:
    with respx.mock() as router:
        router.get(VINTAGES_URL).mock(return_value=httpx.Response(200, json=body))
        with pytest.raises(DataError, match=pattern):
            fred_client().vintage_dates("CPIAUCSL", **WINDOW)


def test_observations_without_list_raises() -> None:
    with respx.mock() as router:
        router.get(f"{API}/series/observations").mock(
            return_value=httpx.Response(200, json={"count": 1})
        )
        with pytest.raises(DataError, match="no observations"):
            fred_client().observations("CPIAUCSL", **CPI_REQUEST)


def test_http_400_without_json_body() -> None:
    with respx.mock() as router:
        router.get(f"{API}/series").mock(return_value=httpx.Response(400, content=b"<html>"))
        with pytest.raises(DataError, match="HTTP 400"):
            fred_client().series("CPIAUCSL")


def test_parse_rejects_bad_rows() -> None:
    with pytest.raises(DataError, match="not an object"):
        parse_observations({"observations": ["x"]}, "X")
    row = {"realtime_start": "2020-01-01", "date": "2020-01-01", "value": "inf"}
    with pytest.raises(DataError, match="not finite"):
        parse_observations({"observations": [row]}, "X")
    meta = {"seriess": [{"id": "CPIAUCSL", "title": "CPI"}]}
    with pytest.raises(DataError, match="missing"):
        parse_series_metadata(meta, CPI)


def test_release_time_must_be_naive() -> None:
    with pytest.raises(ValidationError, match="naive"):
        MacroSeries(id="X", kind="release", release_time=time(8, 30, tzinfo=UTC), description="x")


def test_catalog_iterates_in_order_and_reader_lists_series(tmp_lake: Lake) -> None:
    catalog = MacroCatalog({"CPIAUCSL": CPI})
    assert list(catalog) == ["CPIAUCSL"]
    macro = Macro(tmp_lake, AtlasConfig(), catalog=catalog)
    assert macro.series_info().empty
    tmp_lake.write_table(
        MACRO_SERIES.name,
        parse_series_metadata(
            {
                "seriess": [
                    {
                        "id": "CPIAUCSL",
                        "title": "CPI",
                        "frequency_short": "M",
                        "units": "Index",
                        "seasonal_adjustment_short": "SA",
                    }
                ]
            },
            CPI,
        ),
    )
    assert macro.series_info()["series_id"].tolist() == ["CPIAUCSL"]
