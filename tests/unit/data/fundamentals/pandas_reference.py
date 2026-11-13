"""The pandas implementation of the Stage 09 per-company pipeline as it was before the Phase 2
checkpoint (Tier 3 item 3), kept word for word as the reference the faster implementation must
reproduce exactly (`test_fundamentals_reference.py`). Not used by production code."""

import math
from collections.abc import Sequence
from datetime import timedelta

import numpy as np
import pandas as pd

from atlas.core.config import FundamentalsConfig
from atlas.data.fundamentals.mapping import (
    PERIOD_COLUMNS,
    PERIOD_KEY,
    Alternative,
    Concept,
    ConceptMap,
    Part,
    TaxTag,
)
from atlas.data.fundamentals.pit import HISTORY_COLUMNS, QUARTER_COLUMNS, TTM_COLUMNS, MetricValue

_FACT_KEY = ["taxonomy", "tag", "unit", *PERIOD_KEY]
_DAY = timedelta(days=1)


def _metric_name(concept: Concept) -> str:
    return f"{concept.name}_ttm" if concept.kind == "flow" else concept.name


def latest_filings(facts: pd.DataFrame) -> pd.DataFrame:
    """One row per `(taxonomy, tag, unit, period)`: the one filed last (greatest
    `available_at`, then accession number). Applied to facts known at an as-of date, this is
    "the latest value filed before as_of" for each period: a restatement replaces the
    original only once it is known."""
    ordered = facts.sort_values(["available_at", "accn"], kind="stable")
    return ordered.drop_duplicates(subset=_FACT_KEY, keep="last").reset_index(drop=True)


def _label(tag: TaxTag) -> str:
    namespace, name = tag
    return name if namespace == "us-gaap" else f"{namespace}:{name}"


def _empty_periods() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "period_start": pd.Series(dtype="object"),
            "period_end": pd.Series(dtype="object"),
            "duration_days": pd.Series(dtype="int64"),
            "value": pd.Series(dtype="float64"),
            "available_at": pd.Series(dtype="datetime64[us, UTC]"),
            "accn": pd.Series(dtype="str"),
            "tag": pd.Series(dtype="str"),
        }
    )


def _combine(frames: list[pd.DataFrame], label: str, *, strict: bool) -> pd.DataFrame:
    """Add up per-period values of several inputs (each with `PERIOD_COLUMNS`).

    `strict`: only periods every input has; else every period at least one input has (a
    missing input counts as 0). The row carries the latest `available_at` and its `accn`.
    """
    stacked = pd.concat(frames, ignore_index=True)
    grouped = stacked.groupby(PERIOD_KEY, sort=True)
    out = grouped.agg(
        period_start=("period_start", "first"),
        value=("value", "sum"),
        inputs=("value", "size"),
        available_at=("available_at", "max"),
    ).reset_index()
    latest = stacked.sort_values(["available_at", "accn"], kind="stable").drop_duplicates(
        subset=PERIOD_KEY, keep="last"
    )
    out = out.merge(latest[[*PERIOD_KEY, "accn", "tag"]], on=PERIOD_KEY, how="left")
    if strict:
        out = out[out["inputs"] == len(frames)]
        out["tag"] = label
    else:
        present = stacked.groupby(PERIOD_KEY, sort=True)["tag"].agg("; ".join).reset_index()
        out = out.drop(columns="tag").merge(present, on=PERIOD_KEY, how="left")
    return out[list(PERIOD_COLUMNS)].reset_index(drop=True)


def _alternative(facts: pd.DataFrame, alt: Alternative) -> pd.DataFrame:
    frames = []
    for namespace, name in alt:
        rows = facts[(facts["taxonomy"] == namespace) & (facts["tag"] == name)]
        frames.append(rows.assign(tag=_label((namespace, name)))[list(PERIOD_COLUMNS)])
    if len(frames) == 1:
        return frames[0]
    return _combine(frames, " + ".join(_label(t) for t in alt), strict=True)


def _part(facts: pd.DataFrame, part: Part) -> pd.DataFrame:
    frames = [_alternative(facts, alt).assign(priority=i) for i, alt in enumerate(part)]
    stacked = pd.concat(frames, ignore_index=True).sort_values(
        [*PERIOD_KEY, "priority"], kind="stable"
    )
    first = stacked.drop_duplicates(subset=PERIOD_KEY, keep="first")
    return first[list(PERIOD_COLUMNS)].reset_index(drop=True)


def resolve_concept(facts: pd.DataFrame, concept: Concept) -> pd.DataFrame:
    """The values of `concept` per period from `facts` (`fundamentals_facts` rows).

    Uses the facts in the concept's unit and of its kind (instants for a stock, durations for
    a flow), the latest filing per tag and period (`latest_filings`), then per period the first
    alternative of each part that has a value, summed over the parts present. Returns
    `PERIOD_COLUMNS` sorted by period (end, then length); `tag` names the tag(s) used (`A + B`
    for a strict sum, `A; B` for parts), `available_at`/`accn` the latest input. A concept no
    tag reports gives an empty frame (not an error).
    """
    is_instant = facts["duration_days"] == 0
    usable = facts[(facts["unit"] == concept.unit) & (is_instant == (concept.kind == "stock"))]
    usable = latest_filings(usable[usable["taxonomy"].isin({t[0] for t in concept.tags()})])
    parts = [_part(usable, part) for part in concept.parts]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return _empty_periods()
    out = parts[0] if len(parts) == 1 else _combine(parts, "", strict=False)
    out = out.sort_values(PERIOD_KEY, kind="stable").reset_index(drop=True)
    out["duration_days"] = out["duration_days"].astype("int64")
    out["value"] = out["value"].astype("float64")
    out["available_at"] = out["available_at"].astype("datetime64[us, UTC]")
    return out[list(PERIOD_COLUMNS)]


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
    out["period_start"] = pd.Series([ts.date() for ts in out["s"]], index=out.index, dtype="object")
    out["period_end"] = pd.Series([ts.date() for ts in out["e"]], index=out.index, dtype="object")
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


def _empty_ttm() -> pd.DataFrame:
    return _empty_quarters()[list(TTM_COLUMNS)]


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
    flows = periods[periods["duration_days"] > 0]
    if flows.empty:
        return _empty_ttm()
    annual = _with_ts(flows)
    annual = annual[annual["duration_days"].between(*config.annual_days)]
    candidates = [annual.assign(source="annual", rank=0)]
    q = _with_ts(quarterly_values(flows, config) if quarters is None else quarters)
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
        return _empty_ttm()
    best = found.sort_values(["e", "rank"], kind="stable").drop_duplicates(subset="e", keep="first")
    best = _to_dates(best)
    best["accn"] = best["accn"].astype("str")
    best["tag"] = best["tag"].astype("str")
    return best[list(TTM_COLUMNS)].reset_index(drop=True)


def ttm(periods: pd.DataFrame, config: FundamentalsConfig) -> MetricValue | None:
    """Trailing-twelve-month value of a flow concept (`resolve_concept` output) at the latest
    period end where it can be built (the last row of `ttm_values`), or `None`."""
    values = ttm_values(periods, config)
    if values.empty:
        return None
    last = values.iloc[-1]
    return _metric(last, str(last["source"]))


def latest_balance(periods: pd.DataFrame) -> MetricValue | None:
    """The latest balance of a stock concept (`resolve_concept` output): the instant with the
    greatest `period_end` (`source="reported"`), or `None` when there is none."""
    instants = periods[periods["duration_days"] == 0]
    if instants.empty:
        return None
    return _metric(instants.sort_values("period_end", kind="stable").iloc[-1], "reported")


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


def _flow_history(periods: pd.DataFrame, config: FundamentalsConfig) -> pd.DataFrame:
    q = quarterly_values(periods, config)
    t = ttm_values(periods, config, quarters=q)
    q = q[["period_start", "period_end", "value", "available_at", "source"]]
    t = t[["period_end", "value", "available_at", "source"]].rename(
        columns={"value": "ttm", "available_at": "ttm_available_at", "source": "ttm_source"}
    )
    q = q.astype({"period_end": "object"})
    t = t.astype({"period_end": "object"})
    both = q.merge(t, on="period_end", how="outer").sort_values("period_end", kind="stable")
    both["available_at"] = both[["available_at", "ttm_available_at"]].max(axis=1)
    return both.drop(columns="ttm_available_at")


def _stock_history(periods: pd.DataFrame) -> pd.DataFrame:
    instants = periods[periods["duration_days"] == 0].sort_values("period_end", kind="stable")
    return pd.DataFrame(
        {
            "period_start": None,
            "period_end": instants["period_end"],
            "value": instants["value"],
            "ttm": math.nan,
            "available_at": instants["available_at"],
            "source": "reported",
            "ttm_source": None,
        }
    )


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
    frames = []
    for name in names:
        concept = concept_map[name]
        periods = resolve_concept(known, concept)
        part = _flow_history(periods, config) if concept.kind == "flow" else _stock_history(periods)
        if not part.empty:
            frames.append(part.assign(concept=name))
    if not frames:
        return _empty_history()
    out = pd.concat(frames, ignore_index=True)
    out["value"] = out["value"].astype("float64")
    out["ttm"] = out["ttm"].astype("float64")
    out["available_at"] = pd.to_datetime(out["available_at"], utc=True).astype(
        "datetime64[us, UTC]"
    )
    out["period_start"] = out["period_start"].astype("object")
    out["period_start"] = out["period_start"].where(out["period_start"].notna(), None)
    for col in ("source", "ttm_source"):
        out[col] = out[col].astype("object").where(out[col].notna(), None)
    return out[list(HISTORY_COLUMNS)].reset_index(drop=True)
