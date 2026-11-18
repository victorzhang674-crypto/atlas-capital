"""Stage 29 audit: the tools' edge paths (stale filings, too little history or too few
peers, debt from none, a universe member without an instrument row)."""

import math
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from fundamental_world import CONFIG, FundamentalWorld, build_fundamental_world
from structlog.testing import capture_logs

from atlas.agents.tools import fundamental_tools as ft
from atlas.core.config import AtlasConfig
from atlas.data.view import DataView

LATE = date(2025, 3, 3)
"""More than `data.fundamentals.max_staleness_days` (400) after the last quarter (2023-12-31)."""


def test_stale_filings_are_not_evaluated_and_named_stale(fworld: FundamentalWorld) -> None:
    view = fworld.view(day=LATE)
    flags = {e.id.split(":")[-1]: e.text for e in ft.red_flags(view, "AAA")}
    for check in ("accruals", "receivables", "fcf_debt", "dilution"):
        assert ": NOT EVALUATED. stale or missing filings" in flags[check], check
        assert (
            "the latest period ended 2023-12-31, more than 400 days before 2025-03-03"
            in flags[check]
        )
    assert ": NOT EVALUATED. no MD&A text" in flags["going_concern"]
    margins = {e.id: e.text for e in ft.fundamentals_summary(view, "AAA")}[
        "fsum:AAA:2023-12-31:margins"
    ]
    assert margins.endswith(
        "The latest period ended 2023-12-31, 428 days before 2025-03-03: stale."
    )


def test_thin_history_and_few_peers_are_said_so(fworld: FundamentalWorld) -> None:
    thin = AtlasConfig.model_validate(
        {
            "quant": {"factors": {"min_names": 3}},
            "agents": {"fundamental": {"min_valuation_quarters": 40, "min_sector_names": 10}},
        }
    )
    pe = {e.id: e.text for e in ft.valuation(fworld.view(config=thin), "AAA")}[
        "val:AAA:2024-03-01:pe"
    ]
    assert "own history: 9 quarter(s) with a value, too few to compare" in pe
    assert "Information Technology median: too few peers with a value (4)" in pe


@pytest.mark.parametrize(
    ("now", "before", "growth"),
    [
        (110.0, 100.0, 0.1),
        (5.0, 0.0, math.inf),  # debt from none
        (0.0, 0.0, 0.0),
        (math.nan, 100.0, math.nan),
        (100.0, math.nan, math.nan),
    ],
)
def test_debt_growth(now: float, before: float, growth: float) -> None:
    out = ft._debt_growth(now, before)
    assert out == pytest.approx(growth, nan_ok=True)


def test_a_member_without_an_instrument_row_is_skipped_and_logged(tmp_path: Path) -> None:
    world = build_fundamental_world(tmp_path)
    member = pd.DataFrame(
        {
            "index_id": "SP500",
            "symbol": ["YYY"],
            "start_date": [date(2010, 1, 4)],
            "end_date": [None],
        }
    )
    world.clean.write_table("index_membership", member, mode="upsert")
    view = DataView(world.clean, date(2024, 3, 1), CONFIG)
    with capture_logs() as logs:
        sector = ft.sector_panel(view, "AAA")
    assert sector.peers == ("AAA", "BBB", "CCC", "DDD")
    skipped = [e for e in logs if e["event"] == "fundamental.peer_skipped"]
    assert len(skipped) == 1 and skipped[0]["symbol"] == "YYY"
    assert "no instrument" in skipped[0]["reason"]
