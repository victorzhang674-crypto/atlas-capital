"""Stage 29 follow-up 2 (F5): `DataView.mda`, the latest 10-K MD&A of a symbol known at the
view's as-of (the view's leak, guard and block suites cover it too: `test_view_pit.py`)."""

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from atlas.core.pit import AsOf
from atlas.core.storage import Lake
from atlas.data.filings.text import FILING_TEXT, MdaText
from atlas.data.view import DataView


def row(accn: str, filed: date, stamp: datetime, **kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "symbol": "AAA", "cik": 1, "accn": accn, "form": "10-K", "period_end": date(2023, 12, 31),
        "filed": filed, "accepted_at": stamp, "available_at": stamp, "section": "mda",
        "source": "primary", "document": "a.htm", "url": "https://x/a.htm", "sha256": "0" * 64,
        "status": "ok", "chars": 4, "text": "MD&A",
    }  # fmt: skip
    return {**base, **kw}


def lake_with(tmp_path: Path, *rows: dict[str, object]) -> Lake:
    lake = Lake(tmp_path)
    frame = pd.DataFrame(list(rows))
    for col in ("accepted_at", "available_at"):
        frame[col] = pd.to_datetime(frame[col], utc=True).astype("datetime64[us, UTC]")
    lake.write_table(FILING_TEXT.name, frame, mode="upsert")
    return lake


ACCEPTED = datetime(2024, 2, 2, 22, 0, tzinfo=UTC)


def test_the_latest_known_mda_with_its_filing(tmp_path: Path) -> None:
    lake = lake_with(
        tmp_path,
        row("a-1", date(2023, 2, 3), datetime(2023, 2, 3, 22, tzinfo=UTC), text="old",
            period_end=date(2022, 12, 31)),
        row("a-2", date(2024, 2, 2), ACCEPTED, text="new", source="EX-13", document="ex13.htm"),
        row("b-1", date(2024, 2, 2), ACCEPTED, symbol="BBB", text="other"),
    )  # fmt: skip
    got = DataView(lake, date(2024, 3, 1)).mda(" aaa ")
    assert got == MdaText(
        symbol="AAA",
        form="10-K",
        accn="a-2",
        filed=date(2024, 2, 2),
        period_end=date(2023, 12, 31),
        available_at=ACCEPTED,
        status="ok",
        source="EX-13",
        document="ex13.htm",
        text="new",
    )


def test_an_mda_is_known_only_from_its_acceptance(tmp_path: Path) -> None:
    lake = lake_with(
        tmp_path,
        row("a-1", date(2023, 2, 3), datetime(2023, 2, 3, 22, tzinfo=UTC), text="old"),
        row("a-2", date(2024, 2, 2), ACCEPTED, text="new"),
    )
    on_the_day = AsOf(day=date(2024, 2, 2), cutoff=ACCEPTED)  # known strictly before the cutoff
    assert DataView(lake, on_the_day).mda("AAA").text == "old"  # type: ignore[union-attr]
    after = AsOf(day=date(2024, 2, 2), cutoff=datetime(2024, 2, 2, 22, 0, 1, tzinfo=UTC))
    assert DataView(lake, after).mda("AAA").text == "new"  # type: ignore[union-attr]
    assert DataView(lake, date(2023, 1, 3)).mda("AAA") is None


def test_no_text_is_none_for_a_filing_without_an_mda(tmp_path: Path) -> None:
    lake = lake_with(tmp_path, row("a-1", date(2024, 2, 2), ACCEPTED, status="by_reference",
                                   chars=0, text=None, period_end=None))  # fmt: skip
    got = DataView(lake, date(2024, 3, 1)).mda("AAA")
    assert got is not None
    assert (got.status, got.text, got.period_end) == ("by_reference", None, None)


def test_none_without_the_table_or_a_row(tmp_path: Path) -> None:
    assert DataView(Lake(tmp_path / "empty"), date(2024, 3, 1)).mda("AAA") is None
    lake = lake_with(tmp_path / "one", row("a-1", date(2024, 2, 2), ACCEPTED))
    assert DataView(lake, date(2024, 3, 1)).mda("ZZZ") is None


def test_an_invalid_symbol_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        DataView(Lake(tmp_path), date(2024, 3, 1)).mda("not a ticker!")


def test_each_symbol_gets_its_own_answer_from_one_view(tmp_path: Path) -> None:
    lake = lake_with(
        tmp_path,
        row("a-1", date(2024, 2, 2), ACCEPTED, text="a"),
        row("b-1", date(2024, 2, 2), ACCEPTED, symbol="BBB", text="b"),
    )
    view = DataView(lake, date(2024, 3, 1))
    assert [view.mda(s).text for s in ("AAA", "BBB", "AAA")] == ["a", "b", "a"]  # type: ignore[union-attr]


def test_the_latest_filing_with_an_mda_wins_over_a_later_one_without(tmp_path: Path) -> None:
    """Follow-up 3: a later 10-Q whose MD&A was not found does not hide the 10-K's; with no
    MD&A found at all, the latest filing is returned to say why."""
    later = datetime(2024, 2, 20, 21, 0, tzinfo=UTC)
    lake = lake_with(
        tmp_path,
        row("a-1", date(2024, 2, 2), ACCEPTED, text="10-K text"),
        row("a-2", date(2024, 2, 20), later, form="10-Q", status="not_found", chars=0, text=None),
    )
    got = DataView(lake, date(2024, 3, 1)).mda("AAA")
    assert got is not None and (got.accn, got.text) == ("a-1", "10-K text")
    only_missing = lake_with(
        tmp_path / "two",
        row("b-1", date(2024, 2, 2), ACCEPTED, status="by_reference", chars=0, text=None),
        row("b-2", date(2024, 2, 20), later, form="10-Q", status="not_found", chars=0, text=None),
    )
    got = DataView(only_missing, date(2024, 3, 1)).mda("AAA")
    assert got is not None and (got.accn, got.status) == ("b-2", "not_found")
