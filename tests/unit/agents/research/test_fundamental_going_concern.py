"""Stage 29 follow-up 2 (F6): the going-concern check reads the MD&A of the latest 10-K
known at the as-of (`DataView.mda`), when the 10-K is recent enough and its MD&A was found."""

import shutil
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import pytest
from fundamental_world import AS_OF, CONFIG, FundamentalWorld

from atlas.agents.tools import fundamental_tools as ft
from atlas.core.config import RedFlagsConfig
from atlas.core.storage import Lake
from atlas.data.filings.text import FILING_TEXT
from atlas.data.view import DataView

RF = RedFlagsConfig()
DOUBT = "These conditions raise substantial doubt about our ability to continue as a going concern."


def mda_row(
    accn: str, period_end: date | None, filed: date, *, text: str | None = "Revenue grew.",
    status: str = "ok", accepted: datetime | None = None,
) -> dict[str, object]:  # fmt: skip
    stamp = accepted or datetime.combine(filed, time(21), tzinfo=UTC)
    return {
        "symbol": "AAA", "cik": 1, "accn": accn, "form": "10-K", "period_end": period_end,
        "filed": filed, "accepted_at": stamp, "available_at": stamp, "section": "mda",
        "source": "primary", "document": "a.htm", "url": "https://x/a.htm", "sha256": "0" * 64,
        "status": status, "chars": len(text or ""), "text": text,
    }  # fmt: skip


@pytest.fixture
def lake(fworld: FundamentalWorld, tmp_path: Path) -> Lake:
    """A copy of the world's clean lake this test may write `filing_text` rows to."""
    shutil.copytree(fworld.clean.root, tmp_path / "lake")
    return Lake(tmp_path / "lake", registry=fworld.clean.registry)


def write(lake: Lake, *rows: dict[str, object]) -> None:
    frame = pd.DataFrame(list(rows))
    for col in ("accepted_at", "available_at"):
        frame[col] = pd.to_datetime(frame[col], utc=True).astype("datetime64[us, UTC]")
    lake.write_table(FILING_TEXT.name, frame, mode="upsert")


def going_concern(lake: Lake, day: date = AS_OF) -> tuple[ft.RedFlag, str]:
    view = DataView(lake, day, CONFIG)
    return ft.red_flag_results(view, "AAA", ft._history(view, "AAA"))[-1]


END = AS_OF - timedelta(days=120)
FILED = AS_OF - timedelta(days=60)
PERIODS = f"10-K filed {FILED}, period ended {END}"


def test_without_an_mda_the_check_reads_as_before(lake: Lake) -> None:
    """No `filing_text` table (the recorded fixture's lake): the old reason, word for word."""
    assert going_concern(lake) == (
        ft.RedFlag("going_concern", "NOT EVALUATED", "no MD&A text in the data"),
        "",
    )
    write(lake, {**mda_row("b-1", END, FILED), "symbol": "BBB"})
    assert going_concern(lake)[0].detail == "no MD&A text in the data"  # another symbol's


def test_a_recent_mda_is_scanned_and_its_filing_named(lake: Lake) -> None:
    write(lake, mda_row("a-1", END, FILED, text=f"Revenue grew. {DOUBT}"))
    flag, periods = going_concern(lake)
    assert flag == ft.going_concern_rule(f"Revenue grew. {DOUBT}", RF)
    assert flag.status == "FLAG"
    assert periods == PERIODS


def test_a_clean_mda_is_clear(lake: Lake) -> None:
    write(lake, mda_row("a-1", END, FILED))
    flag, periods = going_concern(lake)
    assert (flag.status, periods) == ("CLEAR", PERIODS)


def test_the_latest_10k_known_at_the_as_of_is_read(lake: Lake) -> None:
    later = AS_OF + timedelta(days=5)
    write(
        lake,
        mda_row("a-0", END - timedelta(days=365), FILED - timedelta(days=365), text=DOUBT),
        mda_row("a-1", END, FILED),
        mda_row("a-2", AS_OF - timedelta(days=1), later, text=DOUBT),  # filed after the as-of
    )
    assert going_concern(lake)[0].status == "CLEAR"
    assert going_concern(lake, later + timedelta(days=5))[0].status == "FLAG"


def test_a_10k_older_than_mda_max_age_days_is_not_evaluated(lake: Lake) -> None:
    edge = AS_OF - timedelta(days=RF.mda_max_age_days)
    write(lake, mda_row("a-1", edge, FILED))
    assert going_concern(lake)[0].status == "CLEAR"  # exactly mda_max_age_days old: read
    stale = edge - timedelta(days=1)
    write(lake, mda_row("a-1", stale, FILED))
    assert going_concern(lake) == (
        ft.RedFlag(
            "going_concern",
            "NOT EVALUATED",
            f"the latest 10-K MD&A covers the period ended {stale}, more than "
            f"{RF.mda_max_age_days} days before {AS_OF}",
        ),
        f"10-K filed {FILED}, period ended {stale}",
    )


def test_without_a_fiscal_year_end_the_filing_date_dates_the_10k(lake: Lake) -> None:
    write(lake, mda_row("a-1", None, FILED))
    assert going_concern(lake) == (
        ft.going_concern_rule("Revenue grew.", RF),
        f"10-K filed {FILED}",
    )
    old = AS_OF - timedelta(days=RF.mda_max_age_days + 1)
    write(lake, mda_row("a-1", None, old))
    assert going_concern(lake)[0].detail == (
        f"the latest 10-K MD&A was filed {old}, more than {RF.mda_max_age_days} days before {AS_OF}"
    )


@pytest.mark.parametrize("status", ["by_reference", "not_found"])
def test_an_mda_not_found_in_the_filing_is_not_evaluated(lake: Lake, status: str) -> None:
    write(lake, mda_row("a-1", END, FILED, text=None, status=status))
    assert going_concern(lake) == (
        ft.RedFlag(
            "going_concern",
            "NOT EVALUATED",
            f"the MD&A of the 10-K filed {FILED} was not found in the filing ({status})",
        ),
        PERIODS,
    )


def test_the_evidence_item_names_the_filing(lake: Lake) -> None:
    write(lake, mda_row("a-1", END, FILED))
    view = DataView(lake, AS_OF, CONFIG)
    item = next(e for e in ft.red_flags(view, "AAA") if e.id.endswith(":going_concern"))
    assert item.text.endswith(f"({PERIODS}).")
    assert ": CLEAR. no MD&A sentence holds all of " in item.text


def test_a_10q_filed_after_the_10k_is_read(lake: Lake) -> None:
    """Follow-up 3: 10-Qs are stored too, so a doubt disclosed in a quarterly report is read as
    soon as the 10-Q is known."""
    q_end, q_filed = AS_OF - timedelta(days=60), AS_OF - timedelta(days=20)
    write(
        lake,
        mda_row("a-1", END, FILED),
        {**mda_row("a-2", q_end, q_filed, text=DOUBT), "form": "10-Q"},
    )
    flag, periods = going_concern(lake)
    assert flag.status == "FLAG"
    assert periods == f"10-Q filed {q_filed}, period ended {q_end}"


def test_a_later_filing_without_an_mda_does_not_hide_an_earlier_one(lake: Lake) -> None:
    q_filed = AS_OF - timedelta(days=20)
    write(
        lake,
        mda_row("a-1", END, FILED, text=DOUBT),
        {
            **mda_row("a-2", AS_OF - timedelta(days=60), q_filed, text=None, status="not_found"),
            "form": "10-Q",
        },
    )
    flag, periods = going_concern(lake)
    assert (flag.status, periods) == ("FLAG", PERIODS)
