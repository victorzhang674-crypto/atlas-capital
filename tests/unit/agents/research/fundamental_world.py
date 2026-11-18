"""The Stage 29 test world: the Stage 16 factor world (twelve filers in three sectors, sixteen
quarters, 600 sessions of bars, a CCC split) plus receivables for every filer and NNN, a
Health Care filer that trips the accruals, receivables, negative-FCF-with-debt and dilution
red flags. A dirty copy adds rows known only after the as-of (a later quarter, later bars,
a late split) that must never show.
"""

import sys
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve()
for _extra in (_HERE.parents[2] / "quant" / "factors", _HERE.parents[1]):
    if str(_extra) not in sys.path:
        sys.path.append(str(_extra))

import pandas as pd  # noqa: E402
from factor_world import (  # noqa: E402
    AS_OF,
    CAL,
    QUARTERS,
    SECTORS,
    STOCKS,
    Company,
    _fact,
    actions_frame,
    build_factor_world,
    company_facts,
    facts_frame,
    filed_after,
    world_bars,
)

from atlas.core.config import AtlasConfig  # noqa: E402
from atlas.core.storage import DEFAULT_REGISTRY, Lake  # noqa: E402
from atlas.core.types.instruments import AssetClass, Instrument  # noqa: E402
from atlas.data.reference import instruments_frame  # noqa: E402
from atlas.data.view import DataView  # noqa: E402

FLAGGED = "NNN"
"""The Health Care filer that trips four red flags."""

RECEIVABLE_DAYS = 0.5
"""Every filer's receivables = this x its quarter's revenue (NNN's grow faster from 2023)."""

NNN = Company(
    symbol=FLAGGED,
    revenue0=500e6,
    growth=0.01,
    gross_margin=0.4,
    op_margin=0.1,
    net_margin=0.15,
    ocf_margin=-0.05,
    capex_ratio=0.05,
    assets=4e9,
    equity=1.5e9,
    debt=1e9,
    cash=2e8,
    shares=1e8,
    share_drift=0.03,
    eps_scale=1.0,
)
"""Net income far above operating cash flow (negative), so accruals are high and free cash
flow negative; shares up 3% a quarter (about 12.6% a year)."""

DEBT_GROWTH = 0.05
"""NNN's long-term debt grows this much a quarter (about 21.6% a year)."""

AR_JUMP = 1.6
"""NNN's receivables in the last four quarters are this times the usual ratio."""

CONFIG = AtlasConfig.model_validate(
    {"quant": {"factors": {"min_names": 3}}, "agents": {"fundamental": {"min_sector_names": 3}}}
)
"""Settings for a world with four names per sector."""


def receivable_rows(c: Company, *, jump_from: int | None = None) -> list[dict[str, Any]]:
    """`AccountsReceivableNetCurrent` at each quarter end of `QUARTERS` (the filing's
    instant), `RECEIVABLE_DAYS` x the quarter's revenue (x `AR_JUMP` from quarter index
    `jump_from` on)."""
    rows = []
    for k, (_, end) in enumerate(QUARTERS):
        revenue = c.revenue0 * (1 + c.growth) ** k
        jump = AR_JUMP if jump_from is not None and k >= jump_from else 1.0
        rows.append(
            _fact(
                c.symbol,
                "AccountsReceivableNetCurrent",
                RECEIVABLE_DAYS * revenue * jump,
                end,
                filed_after(end),
            )
        )
    return rows


def nnn_rows() -> list[dict[str, Any]]:
    """NNN's facts: the factor world's rows with debt growing `DEBT_GROWTH` a quarter."""
    rows = company_facts(NNN)
    for row in rows:
        if row["tag"] == "LongTermDebt":
            k = [end for _, end in QUARTERS].index(row["period_end"])
            row["value"] = NNN.debt * (1 + DEBT_GROWTH) ** k
    return rows


@dataclass(frozen=True)
class FundamentalWorld:
    clean: Lake
    dirty: Lake
    companies: tuple[Company, ...]

    def view(
        self, which: str = "clean", day: date = AS_OF, config: AtlasConfig = CONFIG
    ) -> DataView:
        return DataView(self.clean if which == "clean" else self.dirty, day, config)

    def company(self, symbol: str) -> Company:
        return next(c for c in self.companies if c.symbol == symbol)


def _base(lake: Lake) -> tuple[Company, ...]:
    world = build_factor_world(lake)
    cos = (*world.companies, NNN)
    bars = world_bars([FLAGGED], date(2024, 2, 29), 600, seed=29)
    lake.write_table("prices_daily", bars, mode="upsert")
    instruments = [
        Instrument(symbol=s, asset_class=AssetClass.EQUITY, name=f"{s} Inc", sector=sector)
        for s, sector in {**SECTORS, FLAGGED: "Health Care"}.items()
    ]
    lake.write_table("instruments", instruments_frame(instruments), mode="upsert")
    membership = pd.DataFrame(
        {
            "index_id": "SP500",
            "symbol": [FLAGGED],
            "start_date": [date(2010, 1, 4)],
            "end_date": [None],
        }
    )
    lake.write_table("index_membership", membership, mode="upsert")
    rows = nnn_rows()
    for c in world.companies:
        rows += receivable_rows(c)
    rows += receivable_rows(NNN, jump_from=len(QUARTERS) - 4)
    lake.write_table("fundamentals_facts", facts_frame(rows), mode="upsert")
    return cos


LATER_QUARTER = (date(2024, 1, 1), date(2024, 3, 31))
"""A quarter filed after the as-of (2024-05-06): only the dirty lake has it."""


def _future(lake: Lake, cos: tuple[Company, ...]) -> None:
    """Rows known only after `AS_OF`: Q1 2024 filings, bars to 2024-03-15, an AAA split."""
    rows = []
    for c in cos:
        for row in company_facts(replace(c, revenue0=c.revenue0 * 3), [LATER_QUARTER]):
            rows.append(row)
    lake.write_table("fundamentals_facts", facts_frame(rows), mode="upsert")
    later = list(CAL.sessions(date(2024, 3, 1), date(2024, 3, 15)))
    bars = world_bars([*STOCKS, FLAGGED], later[-1], len(later), seed=99)
    lake.write_table("prices_daily", bars, mode="upsert")
    lake.write_table(
        "corporate_actions",
        actions_frame([("AAA", date(2024, 3, 8), "split", 4.0, None)]),
        mode="upsert",
    )


def build_fundamental_world(root: Path) -> FundamentalWorld:
    """The clean and the dirty lake under `root`."""
    clean = Lake(root / "clean", registry=DEFAULT_REGISTRY.copy())
    cos = _base(clean)
    dirty = Lake(root / "dirty", registry=DEFAULT_REGISTRY.copy())
    _base(dirty)
    _future(dirty, cos)
    return FundamentalWorld(clean=clean, dirty=dirty, companies=cos)
