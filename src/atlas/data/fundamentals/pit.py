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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from typing import Any

import pandas as pd

from atlas.core.config import AtlasConfig, FundamentalsConfig
from atlas.core.errors import DataError
from atlas.core.pit import AsOf, assert_no_lookahead, pit_filter
from atlas.core.storage import Lake
from atlas.core.types.instruments import normalize_symbol
from atlas.data.fundamentals.mapping import (
    Concept,
    ConceptMap,
    FactIndex,
    PeriodRow,
    latest_filings,
    load_concept_map,
    period_rows,
    resolve_rows,
    stamps_column,
    stamps_us,
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


FlowRow = tuple[date, date, float, int, str, str, str]
"""One discrete quarter or TTM value as a plain row: `(period_start, period_end, value,
available_at, accn, tag, source)`, `available_at` in microseconds since the epoch (UTC)."""

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _latest_input(rows: Sequence[tuple[int, str]]) -> tuple[int, str]:
    """The greatest `available_at` of `rows` and the `accn` beside it (the first on a tie)."""
    best = rows[0]
    for row in rows[1:]:
        if row[0] > best[0]:
            best = row
    return best


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


def _first_per_end(rows: Sequence[FlowRow]) -> list[FlowRow]:
    """`rows` without the later rows of a period end already seen."""
    seen: set[date] = set()
    out = []
    for row in rows:
        if row[1] not in seen:
            seen.add(row[1])
            out.append(row)
    return out


def _chain_differences(flows: Sequence[PeriodRow], low: int, high: int) -> list[FlowRow]:
    """Quarters from consecutive cumulative periods that share a start date."""
    chain = sorted(flows, key=lambda r: (r[0], r[1]))  # every flow has a start
    out: list[FlowRow] = []
    for prev, cur in pairwise(chain):
        if prev[0] != cur[0] or not low <= (cur[1] - prev[1]).days <= high:
            continue
        available, accn = _latest_input([(cur[4], cur[5]), (prev[4], prev[5])])
        out.append((prev[1] + _DAY, cur[1], cur[3] - prev[3], available, accn, cur[6], "derived"))
    return out


def _q4_from_three_quarters(
    annual: Sequence[PeriodRow], quarters: Sequence[FlowRow], low: int, high: int
) -> list[FlowRow]:
    """Q4 = FY - (Q1 + Q2 + Q3) where three known quarters tile the year from its start."""
    by_start: dict[date, list[FlowRow]] = {}
    for quarter in quarters:
        by_start.setdefault(quarter[0], []).append(quarter)
    out: list[FlowRow] = []
    for year in annual:  # candidates in the order of the years, then Q1, Q2, Q3
        assert year[0] is not None  # a duration has a start
        for q1 in by_start.get(year[0], ()):
            for q2 in by_start.get(q1[1] + _DAY, ()):
                for q3 in by_start.get(q2[1] + _DAY, ()):
                    if not low <= (year[1] - q3[1]).days <= high:
                        continue
                    available, accn = _latest_input(
                        [(year[4], year[5]), (q1[3], q1[4]), (q2[3], q2[4]), (q3[3], q3[4])]
                    )
                    value = year[3] - q1[2] - q2[2] - q3[2]
                    out.append((q3[1] + _DAY, year[1], value, available, accn, year[6], "derived"))
    return out


def quarter_rows(periods: Sequence[PeriodRow], config: FundamentalsConfig) -> list[FlowRow]:
    """`quarterly_values` on plain rows (`resolve_rows` output), sorted by period end."""
    flows = [r for r in periods if r[2] > 0]
    if not flows:
        return []
    low, high = config.quarter_days
    reported: list[FlowRow] = []
    for start, end, days, value, available, accn, tag in flows:
        if low <= days <= high:
            assert start is not None  # a duration has a start
            reported.append((start, end, value, available, accn, tag, "reported"))
    quarters = _first_per_end([*reported, *_chain_differences(flows, low, high)])
    year_low, year_high = config.annual_days
    annual = [r for r in flows if year_low <= r[2] <= year_high]
    q4 = _q4_from_three_quarters(annual, quarters, low, high)
    quarters = _first_per_end([*quarters, *q4])
    return sorted(quarters, key=lambda r: r[1])


def _flow_frame(rows: Sequence[FlowRow], columns: Sequence[str]) -> pd.DataFrame:
    starts, ends, values, stamps, accns, tags, sources = zip(*rows, strict=True)
    frame = pd.DataFrame(
        {
            "period_start": pd.Series(list(starts), dtype="object"),
            "period_end": pd.Series(list(ends), dtype="object"),
            "value": pd.Series(list(values), dtype="float64"),
            "available_at": stamps_column(stamps),
            "accn": pd.Series(list(accns), dtype="str"),
            "tag": pd.Series(list(tags), dtype="str"),
            "source": pd.Series(list(sources), dtype="str"),
        }
    )
    return frame[list(columns)]


def _flow_rows(frame: pd.DataFrame) -> list[FlowRow]:
    """A `QUARTER_COLUMNS` frame as plain rows (in the frame's order)."""
    if frame.empty:
        return []
    return list(
        zip(
            frame["period_start"].tolist(),
            frame["period_end"].tolist(),
            [float(v) for v in frame["value"].tolist()],
            stamps_us(frame["available_at"]),
            frame["accn"].tolist(),
            frame["tag"].tolist(),
            frame["source"].tolist(),
            strict=True,
        )
    )


def quarterly_values(periods: pd.DataFrame, config: FundamentalsConfig) -> pd.DataFrame:
    """Discrete fiscal quarters of a flow concept (`resolve_concept` output).

    Returns `QUARTER_COLUMNS` sorted by `period_end`, one row per quarter end: reported
    quarters (`source="reported"`), else quarters derived from year-to-date differences or
    from FY - Q1..Q3 (`source="derived"`, `available_at`/`accn` of the latest input). Quarter
    and year lengths come from `config.quarter_days` / `config.annual_days`.
    """
    rows = quarter_rows(period_rows(periods), config)
    return _flow_frame(rows, QUARTER_COLUMNS) if rows else _empty_quarters()


def _metric(row: FlowRow) -> MetricValue:
    start, end, value, available, accn, tag, source = row
    return MetricValue(
        value=value,
        period_start=start,
        period_end=end,
        available_at=_EPOCH + timedelta(microseconds=available),
        accn=str(accn),
        tag=str(tag),
        source=source,
    )


TTM_COLUMNS = ("period_start", "period_end", "value", "available_at", "accn", "tag", "source")
"""Columns of `ttm_values`: one row per period end where a TTM value can be built."""


def _empty_ttm() -> pd.DataFrame:
    return _empty_quarters()[list(TTM_COLUMNS)]


def ttm_rows(
    periods: Sequence[PeriodRow],
    config: FundamentalsConfig,
    *,
    quarters: Sequence[FlowRow] | None = None,
) -> list[FlowRow]:
    """`ttm_values` on plain rows (`resolve_rows` output), sorted by period end."""
    flows = [r for r in periods if r[2] > 0]
    if not flows:
        return []
    year_low, year_high = config.annual_days
    candidates: list[FlowRow] = []
    for start, end, days, value, available, accn, tag in flows:
        if year_low <= days <= year_high:
            assert start is not None  # a duration has a start
            candidates.append((start, end, value, available, accn, tag, "annual"))
    known = quarter_rows(flows, config) if quarters is None else quarters
    by_end: dict[date, list[FlowRow]] = {}
    for quarter in known:
        by_end.setdefault(quarter[1], []).append(quarter)
    summed: list[FlowRow] = []
    for q4 in known:
        for q3 in by_end.get(q4[0] - _DAY, ()):
            for q2 in by_end.get(q3[0] - _DAY, ()):
                for q1 in by_end.get(q2[0] - _DAY, ()):
                    four = (q1, q2, q3, q4)
                    available, accn = _latest_input([(q[3], q[4]) for q in four])
                    value = ((q1[2] + q2[2]) + q3[2]) + q4[2]
                    tag = "; ".join(dict.fromkeys(q[5] for q in four))
                    summed.append((q1[0], q4[1], value, available, accn, tag, "quarters"))
    # the fiscal year ending there, else four quarters: annual candidates come first
    found = _first_per_end(sorted([*candidates, *summed], key=lambda r: (r[1], r[6] != "annual")))
    return found


def ttm_values(
    periods: pd.DataFrame, config: FundamentalsConfig, *, quarters: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Trailing-twelve-month values of a flow concept (`resolve_concept` output) at every
    period end where one can be built, sorted by `period_end` (`TTM_COLUMNS`).

    At each end: the fiscal year ending there (`source="annual"`), else the sum of four
    contiguous discrete quarters ending there (`source="quarters"`). `available_at`/`accn` are
    those of the latest input; `tag` joins the tags used. `quarters` may pass the concept's
    `quarterly_values(periods, config)` when the caller already has it.
    """
    rows = ttm_rows(
        period_rows(periods),
        config,
        quarters=None if quarters is None else _flow_rows(quarters),
    )
    return _flow_frame(rows, TTM_COLUMNS) if rows else _empty_ttm()


def _ttm_metric(periods: Sequence[PeriodRow], config: FundamentalsConfig) -> MetricValue | None:
    rows = ttm_rows(periods, config)
    return _metric(rows[-1]) if rows else None


def _balance_metric(periods: Sequence[PeriodRow]) -> MetricValue | None:
    instants = sorted((r for r in periods if r[2] == 0), key=lambda r: r[1])
    if not instants:
        return None
    _, end, _, value, available, accn, tag = instants[-1]
    return MetricValue(
        value=value,
        period_start=None,
        period_end=end,
        available_at=_EPOCH + timedelta(microseconds=available),
        accn=str(accn),
        tag=str(tag),
        source="reported",
    )


def ttm(periods: pd.DataFrame, config: FundamentalsConfig) -> MetricValue | None:
    """Trailing-twelve-month value of a flow concept (`resolve_concept` output) at the latest
    period end where it can be built (the last row of `ttm_values`), or `None`."""
    return _ttm_metric(period_rows(periods), config)


def latest_balance(periods: pd.DataFrame) -> MetricValue | None:
    """The latest balance of a stock concept (`resolve_concept` output): the instant with the
    greatest `period_end` (`source="reported"`), or `None` when there is none."""
    return _balance_metric(period_rows(periods))


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
    index = FactIndex(known)
    values: dict[str, MetricValue | None] = {}
    for concept in concept_map.values():
        periods = resolve_rows(index, concept)
        found = _ttm_metric(periods, config) if concept.kind == "flow" else _balance_metric(periods)
        values[_metric_name(concept)] = found
    return values


HISTORY_COLUMNS = (
    "concept",
    "period_start",
    "period_end",
    "value",
    "ttm",
    "available_at",
    "source",
    "ttm_source",
)
"""Columns of `concept_history`: one row per concept and fiscal period end."""


def _empty_history() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "concept": pd.Series(dtype="str"),
            "period_start": pd.Series(dtype="object"),
            "period_end": pd.Series(dtype="object"),
            "value": pd.Series(dtype="float64"),
            "ttm": pd.Series(dtype="float64"),
            "available_at": pd.Series(dtype="datetime64[us, UTC]"),
            "source": pd.Series(dtype="object"),
            "ttm_source": pd.Series(dtype="object"),
        }
    )


_HistoryRow = tuple[
    str, date | None, date, float, float, int, str | None, str | None
]  # HISTORY_COLUMNS


def _flow_history(
    name: str, periods: Sequence[PeriodRow], config: FundamentalsConfig
) -> list[_HistoryRow]:
    quarters = quarter_rows(periods, config)
    by_end = {q[1]: q for q in quarters}
    ttm_by_end = {t[1]: t for t in ttm_rows(periods, config, quarters=quarters)}
    out: list[_HistoryRow] = []
    for end in sorted(by_end.keys() | ttm_by_end.keys()):
        quarter, total = by_end.get(end), ttm_by_end.get(end)
        available = max(row[3] for row in (quarter, total) if row is not None)
        out.append(
            (
                name,
                None if quarter is None else quarter[0],
                end,
                math.nan if quarter is None else quarter[2],
                math.nan if total is None else total[2],
                available,
                None if quarter is None else quarter[6],
                None if total is None else total[6],
            )
        )
    return out


def _stock_history(name: str, periods: Sequence[PeriodRow]) -> list[_HistoryRow]:
    instants = sorted((r for r in periods if r[2] == 0), key=lambda r: r[1])
    return [(name, None, r[1], r[3], math.nan, r[4], "reported", None) for r in instants]


def concept_history(
    known: pd.DataFrame,
    concept_map: ConceptMap,
    names: Sequence[str],
    config: FundamentalsConfig,
) -> pd.DataFrame:
    """Every fiscal period of the concepts `names` in `known` (one company's `known_facts`):
    `HISTORY_COLUMNS`, sorted by concept (in `names` order) and `period_end`.

    A flow has one row per end of a discrete quarter (`quarterly_values`: `value`,
    `period_start`, `source`) or of a TTM (`ttm_values`: `ttm`, `ttm_source`), NaN/None for
    the side that is missing; `available_at` is the later of the two. A stock has one row per
    instant (`value` = the balance, `ttm` NaN, `period_start` None). Units are the concept's
    (USD, USD per share, shares). An unknown concept name raises `ValueError`.
    """
    unknown = [n for n in names if n not in concept_map]
    if unknown:
        raise ValueError(f"unknown concept(s) {unknown}; known: {list(concept_map)}")
    index = FactIndex(known)
    rows: list[_HistoryRow] = []
    for name in names:
        concept = concept_map[name]
        periods = resolve_rows(index, concept)
        if concept.kind == "flow":
            rows += _flow_history(name, periods, config)
        else:
            rows += _stock_history(name, periods)
    if not rows:
        return _empty_history()
    concepts, starts, ends, values, totals, stamps, sources, ttm_sources = zip(*rows, strict=True)
    return pd.DataFrame(
        {
            "concept": pd.Series(list(concepts), dtype="str"),
            "period_start": pd.Series(list(starts), dtype="object"),
            "period_end": pd.Series(list(ends), dtype="object"),
            "value": pd.Series(list(values), dtype="float64"),
            "ttm": pd.Series(list(totals), dtype="float64"),
            "available_at": stamps_column(stamps),
            "source": pd.Series(list(sources), dtype="object"),
            "ttm_source": pd.Series(list(ttm_sources), dtype="object"),
        }
    )[list(HISTORY_COLUMNS)]


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
