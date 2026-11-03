"""Point-in-time fundamentals: discrete quarters, TTM, latest balances and the wide view.

Flows (income and cash-flow lines) are reported over durations: a 10-Q gives the quarter and
the year to date (cash-flow lines often only the year to date), a 10-K the fiscal year.
`quarterly_values` turns them into discrete fiscal quarters: the reported quarters, then the
difference of consecutive year-to-date periods sharing a start (H1 - Q1, 9M - H1, FY - 9M),
then Q4 = FY - (Q1 + Q2 + Q3) when three quarters tile the fiscal year's first nine months.
A reported quarter beats a derived one. `ttm` is the trailing twelve months at the latest
period end where it can be built (the fiscal year ending there, else four contiguous
quarters); `latest_balance` is a stock's latest instant. Units are those of the concept (USD,
USD per share, shares); a derived EPS quarter is only approximate (share counts move).
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from atlas.core.config import AtlasConfig, FundamentalsConfig
from atlas.core.errors import DataError
from atlas.core.pit import AsOf, assert_no_lookahead, pit_filter
from atlas.core.storage import Lake
from atlas.core.types.instruments import normalize_symbol
from atlas.data.fundamentals.mapping import (
    Concept,
    ConceptMap,
    latest_filings,
    load_concept_map,
    resolve_concept,
)
from atlas.data.fundamentals.parser import FUNDAMENTALS_FACTS

QUARTER_COLUMNS = (
    "period_start",
    "period_end",
    "value",
    "available_at",
    "accn",
    "tag",
    "source",
)
"""Columns of `quarterly_values`: one row per fiscal quarter (by its end date)."""

_DAY = timedelta(days=1)


@dataclass(frozen=True)
class MetricValue:
    """One standardised value and where it came from.

    `value` is in the concept's unit (USD, USD per share, shares; ratios are fractions);
    `period_start`/`period_end` the period it covers (`period_start` is `None` for a balance);
    `available_at` (UTC) when its latest input became public and `accn` that filing; `tag`
    the XBRL tag(s) used; `source` how it was obtained (`reported`, `annual`, `quarters`,
    `ratio`).
    """

    value: float
    period_start: date | None
    period_end: date
    available_at: datetime
    accn: str
    tag: str
    source: str


def _with_ts(periods: pd.DataFrame) -> pd.DataFrame:
    """`periods` plus naive Timestamp columns `s`/`e` for date arithmetic."""
    out = periods.copy()
    out["s"] = pd.to_datetime(out["period_start"])
    out["e"] = pd.to_datetime(out["period_end"])
    return out


def _latest(frame: pd.DataFrame, prefixes: list[str]) -> tuple[pd.Series, pd.Series]:
    """Per row, the greatest `<p>available_at` over `prefixes` and the `<p>accn` beside it."""
    stamps = np.stack(
        [frame[f"{p}available_at"].to_numpy(dtype="datetime64[us]") for p in prefixes], axis=1
    )
    pick = stamps.argmax(axis=1)
    rows = np.arange(len(frame))
    accns = np.stack([frame[f"{p}accn"].to_numpy(dtype=object) for p in prefixes], axis=1)
    available = pd.Series(
        pd.to_datetime(stamps[rows, pick]).tz_localize("UTC"), index=frame.index
    ).astype("datetime64[us, UTC]")
    return available, pd.Series(accns[rows, pick], index=frame.index, dtype="str")


def _empty_quarters() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "period_start": pd.Series(dtype="object"),
            "period_end": pd.Series(dtype="object"),
            "value": pd.Series(dtype="float64"),
            "available_at": pd.Series(dtype="datetime64[us, UTC]"),
            "accn": pd.Series(dtype="str"),
            "tag": pd.Series(dtype="str"),
            "source": pd.Series(dtype="str"),
        }
    )


def _chain_differences(p: pd.DataFrame, low: int, high: int) -> pd.DataFrame:
    """Quarters from consecutive cumulative periods that share a start date."""
    chain = p.sort_values(["s", "e"], kind="stable")
    prev = chain.groupby("s", sort=False)[["e", "value", "available_at", "accn"]].shift(1)
    gap = (chain["e"] - prev["e"]).dt.days
    ok = prev["e"].notna() & gap.between(low, high)
    cur, before = chain[ok], prev[ok].add_prefix("p_")
    both = pd.concat([cur, before], axis=1)
    available, accn = _latest(both, ["", "p_"])
    return pd.DataFrame(
        {
            "s": before["p_e"] + _DAY,
            "e": cur["e"],
            "value": cur["value"] - before["p_value"],
            "available_at": available,
            "accn": accn,
            "tag": cur["tag"],
            "source": "derived",
        }
    )


def _q4_from_three_quarters(
    annual: pd.DataFrame, quarters: pd.DataFrame, low: int, high: int
) -> pd.DataFrame:
    """Q4 = FY - (Q1 + Q2 + Q3) where three known quarters tile the year from its start."""
    cols = ["s", "e", "value", "available_at", "accn"]
    m = annual[[*cols, "tag"]].add_prefix("a_")
    for i, key in ((1, "a_s"), (2, "n2"), (3, "n3")):
        m = m.merge(quarters[cols].add_prefix(f"q{i}_"), left_on=key, right_on=f"q{i}_s")
        m[f"n{i + 1}"] = m[f"q{i}_e"] + _DAY
    days = (m["a_e"] - m["q3_e"]).dt.days
    m = m[days.between(low, high)]
    available, accn = _latest(m, ["a_", "q1_", "q2_", "q3_"])
    return pd.DataFrame(
        {
            "s": m["n4"],
            "e": m["a_e"],
            "value": m["a_value"] - m["q1_value"] - m["q2_value"] - m["q3_value"],
            "available_at": available,
            "accn": accn,
            "tag": m["a_tag"],
            "source": "derived",
        }
    )


def _to_dates(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["period_start"] = [ts.date() for ts in out["s"]]
    out["period_end"] = [ts.date() for ts in out["e"]]
    out["value"] = out["value"].astype("float64")
    out["available_at"] = out["available_at"].astype("datetime64[us, UTC]")
    return out


def quarterly_values(periods: pd.DataFrame, config: FundamentalsConfig) -> pd.DataFrame:
    """Discrete fiscal quarters of a flow concept (`resolve_concept` output).

    Returns `QUARTER_COLUMNS` sorted by `period_end`, one row per quarter end: reported
    quarters (`source="reported"`), else quarters derived from year-to-date differences or
    from FY - Q1..Q3 (`source="derived"`, `available_at`/`accn` of the latest input). Quarter
    and year lengths come from `config.quarter_days` / `config.annual_days`.
    """
    flows = periods[periods["duration_days"] > 0]
    if flows.empty:
        return _empty_quarters()
    p = _with_ts(flows)
    low, high = config.quarter_days
    reported = p[p["duration_days"].between(low, high)].assign(source="reported")
    keep = ["s", "e", "value", "available_at", "accn", "tag", "source"]
    quarters = pd.concat(
        [reported[keep], _chain_differences(p, low, high)[keep]], ignore_index=True
    ).drop_duplicates(subset="e", keep="first")
    annual = p[p["duration_days"].between(*config.annual_days)]
    q4 = _q4_from_three_quarters(annual, quarters, low, high)
    quarters = pd.concat([quarters, q4[keep]], ignore_index=True).drop_duplicates(
        subset="e", keep="first"
    )
    quarters = _to_dates(quarters.sort_values("e", kind="stable"))
    return quarters[list(QUARTER_COLUMNS)].reset_index(drop=True)


def _metric(row: pd.Series, source: str, tag: str | None = None) -> MetricValue:
    start = row["period_start"]
    return MetricValue(
        value=float(row["value"]),
        period_start=None if start is None or pd.isna(start) else start,
        period_end=row["period_end"],
        available_at=pd.Timestamp(row["available_at"]).to_pydatetime(),
        accn=str(row["accn"]),
        tag=str(row["tag"]) if tag is None else tag,
        source=source,
    )


def ttm(periods: pd.DataFrame, config: FundamentalsConfig) -> MetricValue | None:
    """Trailing-twelve-month value of a flow concept (`resolve_concept` output), or `None`.

    At the latest period end where it can be built: the fiscal year ending there
    (`source="annual"`), else the sum of four contiguous discrete quarters ending there
    (`source="quarters"`). `available_at`/`accn` are those of the latest input.
    """
    flows = periods[periods["duration_days"] > 0]
    if flows.empty:
        return None
    annual = _with_ts(flows)
    annual = annual[annual["duration_days"].between(*config.annual_days)]
    candidates = [annual.assign(source="annual", rank=0)]
    q = _with_ts(quarterly_values(flows, config))
    cols = ["s", "e", "value", "available_at", "accn", "tag"]
    m = q[cols].add_prefix("q4_")
    for i in (3, 2, 1):
        m[f"p{i}"] = m[f"q{i + 1}_s"] - _DAY
        m = m.merge(q[cols].add_prefix(f"q{i}_"), left_on=f"p{i}", right_on=f"q{i}_e")
    if not m.empty:
        available, accn = _latest(m, ["q1_", "q2_", "q3_", "q4_"])
        tags = m[["q1_tag", "q2_tag", "q3_tag", "q4_tag"]].to_numpy()
        candidates.append(
            pd.DataFrame(
                {
                    "s": m["q1_s"],
                    "e": m["q4_e"],
                    "value": m[["q1_value", "q2_value", "q3_value", "q4_value"]].sum(axis=1),
                    "available_at": available,
                    "accn": accn,
                    "tag": ["; ".join(dict.fromkeys(t)) for t in tags],
                    "source": "quarters",
                    "rank": 1,
                }
            )
        )
    found = pd.concat(candidates, ignore_index=True)
    if found.empty:
        return None
    best = found.sort_values(["e", "rank"], ascending=[False, True], kind="stable").iloc[0]
    best = best.copy()
    best["period_start"] = best["s"].date()
    best["period_end"] = best["e"].date()
    return _metric(best, str(best["source"]))


def latest_balance(periods: pd.DataFrame) -> MetricValue | None:
    """The latest balance of a stock concept (`resolve_concept` output): the instant with the
    greatest `period_end` (`source="reported"`), or `None` when there is none."""
    instants = periods[periods["duration_days"] == 0]
    if instants.empty:
        return None
    return _metric(instants.sort_values("period_end", kind="stable").iloc[-1], "reported")


def known_facts(facts: pd.DataFrame, as_of: AsOf) -> pd.DataFrame:
    """The facts known at `as_of`, one per tag and period: the latest filing among those whose
    `available_at` is strictly before `as_of.cutoff`.

    So an earlier `as_of` sees the value as originally filed and a later one the restated
    value, once the restating filing (a 10-K/A, a 10-Q/A or a later comparative) is public.
    A fact whose period ends after its filing date is a filer error (no period can end after
    the report that states it) and is left out, never re-dated (Stage 13's
    `fundamentals_period_after_filing` check reports it). The result is checked with
    `assert_no_lookahead` (`LookAheadError` if a later fact slipped through).
    """
    known = latest_filings(pit_filter(_possible(facts), as_of))
    assert_no_lookahead(known, as_of)
    return known


def known_fact_count(facts: pd.DataFrame, as_of: AsOf) -> int:
    """How many of `facts` `known_facts` may use at `as_of` (before keeping the latest filing
    per period). Knowledge only grows with the cutoff, so two as-of moments with the same
    count see the same facts: a cache key for `concept_values`."""
    return len(pit_filter(_possible(facts), as_of))


def _possible(facts: pd.DataFrame) -> pd.DataFrame:
    """`facts` without the filer errors whose period ends after their own filing."""
    return facts[(facts["period_end"] <= facts["filed"]).to_numpy(dtype=bool)]


# --- the wide point-in-time view ---------------------------------------------------------------

RATIO_NAMES = ("gross_margin", "operating_margin", "roe", "roa", "debt_to_equity", "fcf_ttm")
"""Derived metrics, after the concepts in the wide view (see `compute_ratios`)."""

DETAIL_COLUMNS = (
    "metric",
    "value",
    "period_start",
    "period_end",
    "available_at",
    "accn",
    "tag",
    "source",
)
"""Columns of `fundamentals_pit_detail`: one row per metric."""

_QUOTIENTS = (
    ("gross_margin", "gross_profit_ttm", "revenue_ttm"),
    ("operating_margin", "operating_income_ttm", "revenue_ttm"),
    ("roe", "net_income_ttm", "stockholders_equity"),
    ("roa", "net_income_ttm", "total_assets"),
    ("debt_to_equity", "total_debt", "stockholders_equity"),
)
_FCF = ("fcf_ttm", "operating_cash_flow_ttm", "capex_ttm")


def _metric_name(concept: Concept) -> str:
    return f"{concept.name}_ttm" if concept.kind == "flow" else concept.name


def metric_names(concept_map: ConceptMap) -> tuple[str, ...]:
    """Index of the wide view: `<flow>_ttm` and `<stock>` per concept in map order, then
    `RATIO_NAMES`."""
    return (*(_metric_name(c) for c in concept_map.values()), *RATIO_NAMES)


def _ratio_inputs(name: str) -> tuple[str, str]:
    if name == _FCF[0]:
        return _FCF[1], _FCF[2]
    return next((num, den) for n, num, den in _QUOTIENTS if n == name)


def compute_ratios(values: Mapping[str, float]) -> dict[str, float]:
    """The derived metrics from the wide view's values, as fractions (FCF in USD).

    Margins are over TTM revenue; ROE = TTM net income / latest equity; ROA = TTM net income
    / latest total assets; debt/equity = total debt / equity (latest balances, not averages);
    FCF = TTM operating cash flow - TTM capex. A ratio whose denominator is missing (NaN) or
    not positive, or whose numerator is missing, is NaN.
    """
    out: dict[str, float] = {}
    for name, num_key, den_key in _QUOTIENTS:
        num, den = values.get(num_key, math.nan), values.get(den_key, math.nan)
        out[name] = num / den if not (math.isnan(num) or math.isnan(den)) and den > 0 else math.nan
    ocf, capex = values.get(_FCF[1], math.nan), values.get(_FCF[2], math.nan)
    out[_FCF[0]] = ocf - capex if not (math.isnan(ocf) or math.isnan(capex)) else math.nan
    return {name: out[name] for name in RATIO_NAMES}


def _row(metric: str, value: MetricValue | None, source: str | None = None) -> dict[str, Any]:
    if value is None:
        return {"metric": metric, "value": math.nan, "period_start": None, "period_end": None,
                "available_at": pd.NaT, "accn": None, "tag": None, "source": None}  # fmt: skip
    return {
        "metric": metric,
        "value": value.value if source is None else math.nan,
        "period_start": value.period_start,
        "period_end": value.period_end,
        "available_at": pd.Timestamp(value.available_at),
        "accn": value.accn,
        "tag": value.tag,
        "source": value.source if source is None else source,
    }


def concept_values(
    known: pd.DataFrame, concept_map: ConceptMap, config: FundamentalsConfig
) -> dict[str, MetricValue | None]:
    """Each concept's value from `known` (one company's `known_facts`), by metric name in
    concept-map order: a flow's TTM (`ttm`), a stock's latest balance (`latest_balance`),
    `None` where there is none. It does not depend on the as-of day (staleness and ratios are
    `assemble_detail`'s), so it can be reused for as long as the known facts are the same."""
    values: dict[str, MetricValue | None] = {}
    for concept in concept_map.values():
        periods = resolve_concept(known, concept)
        found = ttm(periods, config) if concept.kind == "flow" else latest_balance(periods)
        values[_metric_name(concept)] = found
    return values


def fundamentals_pit_detail(
    facts: pd.DataFrame, as_of: AsOf, concept_map: ConceptMap, config: FundamentalsConfig
) -> pd.DataFrame:
    """The wide view with its sources: `DETAIL_COLUMNS`, one row per `metric_names` entry.

    From the facts known at `as_of` (`known_facts`): each flow's TTM (`ttm`) and each stock's
    latest balance (`latest_balance`), then `compute_ratios`. A metric whose period ended more
    than `config.max_staleness_days` before `as_of.day` is NaN with `source="stale"`; one with
    no data is NaN with no source. A ratio row cites its latest input (`source="ratio"`).
    """
    values = concept_values(known_facts(facts, as_of), concept_map, config)
    return assemble_detail(values, as_of, config)


def assemble_detail(
    values: Mapping[str, MetricValue | None], as_of: AsOf, config: FundamentalsConfig
) -> pd.DataFrame:
    """`fundamentals_pit_detail` from `concept_values`: the staleness rule at `as_of.day`, then
    the ratios of the values that are not stale."""
    floor = as_of.day - timedelta(days=config.max_staleness_days)
    used: dict[str, MetricValue] = {}
    rows = []
    for name, found in values.items():
        if found is not None and found.period_end < floor:
            rows.append(_row(name, found, source="stale"))
            continue
        if found is not None:
            used[name] = found
        rows.append(_row(name, found))
    ratios = compute_ratios({name: v.value for name, v in used.items()})
    for name, value in ratios.items():
        inputs = [used[k] for k in _ratio_inputs(name) if k in used]
        if math.isnan(value) or not inputs:
            rows.append(_row(name, None) | {"source": "ratio"})
            continue
        latest = max(inputs, key=lambda v: v.available_at)
        rows.append(
            _row(
                name,
                MetricValue(
                    value=value,
                    period_start=None,
                    period_end=max(v.period_end for v in inputs),
                    available_at=latest.available_at,
                    accn=latest.accn,
                    tag="",
                    source="ratio",
                ),
            )
        )
    detail = pd.DataFrame(rows, columns=list(DETAIL_COLUMNS))
    detail["value"] = detail["value"].astype("float64")
    detail["available_at"] = pd.to_datetime(detail["available_at"], utc=True).astype(
        "datetime64[us, UTC]"
    )
    return detail


def fundamentals_pit(
    facts: pd.DataFrame, as_of: AsOf, concept_map: ConceptMap, config: FundamentalsConfig
) -> pd.Series:
    """The wide point-in-time view of one company's `facts` (`fundamentals_facts` rows) at
    `as_of`: a float Series indexed by `metric_names(concept_map)` (USD, USD per share, shares;
    ratios as fractions; NaN where unavailable). See `fundamentals_pit_detail` for sources."""
    detail = fundamentals_pit_detail(facts, as_of, concept_map, config)
    return pd.Series(detail["value"].to_numpy(dtype="float64"), index=list(detail["metric"]))


class Fundamentals:
    """Point-in-time fundamentals read from the lake's `fundamentals_facts` table.

    `config` defaults to `AtlasConfig()` (pass the run's loaded config: its calendar sets the
    decision time of a plain `date` as-of, its `data.fundamentals` the quarter/year lengths and
    staleness); `concept_map` to `config.data.fundamentals.xbrl_map_file` (relative to the
    working directory).
    """

    def __init__(
        self,
        lake: Lake,
        config: AtlasConfig | None = None,
        *,
        concept_map: ConceptMap | None = None,
    ) -> None:
        self._lake = lake
        self._cfg = config if config is not None else AtlasConfig()
        self.concept_map = (
            concept_map
            if concept_map is not None
            else load_concept_map(self._cfg.data.fundamentals.xbrl_map_file)
        )

    def _as_of(self, as_of: date | AsOf) -> AsOf:
        if isinstance(as_of, AsOf):
            return as_of
        if isinstance(as_of, datetime):
            raise TypeError("pass a trading day (date) or an AsOf, not a datetime")
        return AsOf.for_session(as_of, config=self._cfg.calendar)

    def facts(self, symbol: str) -> pd.DataFrame:
        """Every stored fact of `symbol` (all filings, including ones after any as-of date).

        Raises `DataError` when the table has never been written (run `ingest_fundamentals`).
        """
        if not self._lake.has_table(FUNDAMENTALS_FACTS.name):
            raise DataError("fundamentals_facts is empty: run ingest_fundamentals first")
        return self._lake.read_table(
            FUNDAMENTALS_FACTS.name, filters=[("symbol", "==", normalize_symbol(symbol))]
        )

    def pit_detail(self, symbol: str, as_of: date | AsOf) -> pd.DataFrame:
        """`fundamentals_pit_detail` of `symbol` at `as_of` (a date uses the configured
        decision time)."""
        point = self._as_of(as_of)
        return fundamentals_pit_detail(
            self.facts(symbol), point, self.concept_map, self._cfg.data.fundamentals
        )

    def pit(self, symbol: str, as_of: date | AsOf) -> pd.Series:
        """The wide point-in-time view of `symbol` at `as_of` (`fundamentals_pit`; the Series
        is named after the canonical symbol; all NaN for a symbol with no stored facts)."""
        point = self._as_of(as_of)
        series = fundamentals_pit(
            self.facts(symbol), point, self.concept_map, self._cfg.data.fundamentals
        )
        series.name = normalize_symbol(symbol)
        return series
