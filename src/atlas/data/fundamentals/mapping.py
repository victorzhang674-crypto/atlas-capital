"""Concept map: standardised fundamentals defined over XBRL tags (`config/xbrl_map.yaml`).

A concept (e.g. `revenue`) is a `flow` (a duration: summed into quarters and TTM), a `stock`
(an instant: latest balance) or an `average` (a quantity averaged over a duration, such as
weighted-average shares: the latest period's value is used and periods are never added or
subtracted), in one XBRL `unit`. Its value for a period is the sum of its
**parts** that have a value (a missing part counts as 0; no part present → no value). A part is
a fallback list of **alternatives**, tried in order; an alternative is one tag or a strict sum
of tags (`"A + B"`, present only when every term has a value for that period). A concept
written with `tags:` has one part; one written with `sum_of:` has several.

Tags are `(taxonomy, name)` pairs: a bare name in the file is `us-gaap`, `dei:Name` is `dei`.
"""

import math
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Literal, Self

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from atlas.core.config import read_config_file
from atlas.core.errors import ConfigError

TaxTag = tuple[str, str]
"""An XBRL tag: `(taxonomy, name)`, e.g. `("us-gaap", "Revenues")`."""

Alternative = tuple[TaxTag, ...]
"""One alternative of a part: a single tag, or several tags whose values are summed."""

Part = tuple[Alternative, ...]
"""A fallback list of alternatives; the first with a value for a period wins."""

Kind = Literal["flow", "stock", "average"]

TAXONOMIES = ("us-gaap", "dei")
_TERM_RE = re.compile(r"^(?:(?P<ns>[a-z][a-z-]*):)?(?P<name>[A-Z][A-Za-z0-9]*)$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

RATIO_INPUTS: Mapping[str, Kind] = {
    "revenue": "flow",
    "gross_profit": "flow",
    "operating_income": "flow",
    "net_income": "flow",
    "operating_cash_flow": "flow",
    "capex": "flow",
    "total_assets": "stock",
    "stockholders_equity": "stock",
    "total_debt": "stock",
}
"""Concepts the derived ratios need, with their kind; every concept map must define them."""


def parse_alternative(text: str) -> Alternative:
    """`"A + dei:B"` → `(("us-gaap", "A"), ("dei", "B"))`. `ValueError` on a bad term or a
    taxonomy other than us-gaap/dei."""
    terms = []
    for raw in text.split("+"):
        match = _TERM_RE.match(raw.strip())
        if match is None:
            raise ValueError(f"{text!r}: {raw.strip()!r} is not an XBRL tag name")
        namespace = match["ns"] or "us-gaap"
        if namespace not in TAXONOMIES:
            raise ValueError(f"{text!r}: taxonomy {namespace!r} is not one of {TAXONOMIES}")
        terms.append((namespace, match["name"]))
    if len(set(terms)) != len(terms):
        raise ValueError(f"{text!r}: a tag appears twice")
    return tuple(terms)


class Concept(BaseModel):
    """One standardised concept: `kind`, `unit` and its `parts` (see the module docstring)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    kind: Kind
    unit: str = Field(min_length=1)
    parts: tuple[Part, ...] = Field(min_length=1)

    def tags(self) -> frozenset[TaxTag]:
        """Every tag the concept reads."""
        return frozenset(tag for part in self.parts for alt in part for tag in alt)


class _ConceptEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Kind
    unit: str = Field(min_length=1)
    tags: tuple[str, ...] | None = Field(default=None, min_length=1)
    sum_of: tuple[tuple[str, ...], ...] | None = Field(default=None, min_length=1)

    @field_validator("sum_of")
    @classmethod
    def _parts_not_empty(
        cls, value: tuple[tuple[str, ...], ...] | None
    ) -> tuple[tuple[str, ...], ...] | None:
        if value is not None and any(not part for part in value):
            raise ValueError("every sum_of part needs at least one alternative")
        return value

    @model_validator(mode="after")
    def _one_definition(self) -> Self:
        if (self.tags is None) == (self.sum_of is None):
            raise ValueError("give exactly one of `tags` or `sum_of`")
        return self

    def parts(self) -> tuple[Part, ...]:
        """The entry's parts: `tags` as one part, else the `sum_of` parts (parsed)."""
        raw = (self.tags,) if self.tags is not None else self.sum_of
        assert raw is not None
        return tuple(tuple(parse_alternative(alt) for alt in part) for part in raw)


class _ConceptMapFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    concepts: dict[str, _ConceptEntry] = Field(min_length=1)


class ConceptMap(Mapping[str, Concept]):
    """The concepts of a concept map, in file order (read-only mapping name → `Concept`)."""

    def __init__(self, concepts: Mapping[str, Concept]) -> None:
        """A map of already validated concepts (use `load_concept_map` for a file)."""
        self._concepts = dict(concepts)

    def __getitem__(self, name: str) -> Concept:
        return self._concepts[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._concepts)

    def __len__(self) -> int:
        return len(self._concepts)

    @property
    def names(self) -> tuple[str, ...]:
        """Concept names in file order."""
        return tuple(self._concepts)

    def tags(self) -> frozenset[TaxTag]:
        """Every tag any concept reads (the tags the facts table stores)."""
        return frozenset(tag for concept in self._concepts.values() for tag in concept.tags())


def load_concept_map(path: Path) -> ConceptMap:
    """The concept map in `path` (`concepts: {name: {kind, unit, tags | sum_of}}`).

    Raises `ConfigError` naming the file and the entry at fault for invalid content, a missing
    file, or a map that lacks a ratio input (`RATIO_INPUTS`) or defines it with the wrong kind.
    """
    data = read_config_file(path)
    try:
        body = _ConceptMapFile.model_validate(data)
        concepts = {}
        for name, entry in body.concepts.items():
            if not _NAME_RE.match(name):
                raise ValueError(f"concept name {name!r} must be lower_snake_case")
            concepts[name] = Concept(
                name=name, kind=entry.kind, unit=entry.unit, parts=entry.parts()
            )
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"{path}: {problems}") from None
    except ValueError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    for name, kind in RATIO_INPUTS.items():
        if name not in concepts:
            raise ConfigError(f"{path}: concept {name!r} is required (ratio input)")
        if concepts[name].kind != kind:
            raise ConfigError(f"{path}: concept {name!r} must be a {kind}")
    return ConceptMap(concepts)


# --- resolving a concept per period ------------------------------------------------------------

PERIOD_KEY = ["period_end", "duration_days"]
"""A reporting period: its end date and length in days (0 = an instant)."""

PERIOD_COLUMNS = (
    "period_start",
    "period_end",
    "duration_days",
    "value",
    "available_at",
    "accn",
    "tag",
)
"""Columns of a resolved concept: one row per period (`resolve_concept`)."""

_FACT_KEY = ["taxonomy", "tag", "unit", *PERIOD_KEY]


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


# One company has a few thousand facts and sixteen concepts, each resolved from a handful of
# rows. As pandas frames that is thousands of tiny filters, sorts and merges per company
# (about 0.2 s; minutes for a 500-name cross-section), so the work is done on plain rows and
# only the public functions build frames. `tests/unit/data/fundamentals/pandas_reference.py`
# keeps the frame-based implementation; the two are compared value for value.

PeriodRow = tuple[date | None, date, int, float, int, str, str]
"""One resolved period as a plain row: `(period_start, period_end, duration_days, value,
available_at, accn, tag)`, `available_at` in microseconds since the epoch (UTC)."""

_PeriodKey = tuple[date, int]
_Fact = tuple[date | None, date, int, float, int, str]


def _null(value: object) -> bool:
    return value is None or value is pd.NaT or (isinstance(value, float) and math.isnan(value))


def stamps_us(column: pd.Series) -> list[int]:
    """A tz-aware timestamp column as microseconds since the epoch (UTC)."""
    stamps = pd.to_datetime(column, utc=True).dt.tz_localize(None)
    values: list[int] = stamps.to_numpy(dtype="datetime64[us]").astype("int64").tolist()
    return values


def stamps_column(values: Sequence[int]) -> pd.Series:
    """Microseconds since the epoch as a `datetime64[us, UTC]` column."""
    array = np.asarray(list(values), dtype="int64").astype("datetime64[us]")
    return pd.Series(pd.DatetimeIndex(array).tz_localize("UTC")).astype("datetime64[us, UTC]")


def kahan_sum(values: Sequence[float]) -> float:
    """The compensated sum pandas' grouped `sum` computes (the same operations in the same
    order, so the same float)."""
    total, comp = 0.0, 0.0
    for value in values:
        y = value - comp
        t = total + y
        comp = t - total - y
        total = t
    return total


class FactIndex:
    """One company's facts, ready for `resolve_rows`: the latest filing per tag, unit and
    period (`latest_filings`: greatest `available_at`, then accession number, then the later
    row), grouped by `(taxonomy, tag, unit, instant or duration)`."""

    __slots__ = ("_groups",)

    def __init__(self, facts: pd.DataFrame) -> None:
        groups: dict[tuple[str, str, str, bool], dict[_PeriodKey, _Fact]] = {}
        if len(facts):
            taxonomy = facts["taxonomy"].tolist()
            tag = facts["tag"].tolist()
            unit = facts["unit"].tolist()
            start = facts["period_start"].tolist()
            end = facts["period_end"].tolist()
            duration = facts["duration_days"].tolist()
            value = facts["value"].tolist()
            available = stamps_us(facts["available_at"])
            accn = facts["accn"].tolist()
            order = sorted(range(len(taxonomy)), key=lambda i: (available[i], accn[i]))
            for i in reversed(order):  # the latest filing of a period is met first
                days = int(duration[i])
                group = groups.setdefault((taxonomy[i], tag[i], unit[i], days == 0), {})
                key = (end[i], days)
                if key not in group:
                    first = None if _null(start[i]) else start[i]
                    group[key] = (first, end[i], days, float(value[i]), available[i], accn[i])
        self._groups = groups

    def tag_rows(self, tag: TaxTag, unit: str, *, instant: bool) -> dict[_PeriodKey, PeriodRow]:
        """The periods one tag reports in `unit` (instants or durations), by period."""
        label = _label(tag)
        found = self._groups.get((tag[0], tag[1], unit, instant), {})
        return {key: (*fact, label) for key, fact in found.items()}


def _combine_rows(
    frames: Sequence[Mapping[_PeriodKey, PeriodRow]], label: str | None
) -> dict[_PeriodKey, PeriodRow]:
    """Add up per-period rows of several inputs. With a `label` (a strict sum, `A + B`): only
    periods every input has, tagged `label`. Without: every period at least one input has (a
    missing input counts as 0), tagged with the inputs' tags joined by `; `. The row carries
    the first known `period_start`, the latest `available_at` and the `accn` of the input
    filed last (ties: the later input)."""
    members: dict[_PeriodKey, list[PeriodRow]] = {}
    for frame in frames:
        for key, row in frame.items():
            members.setdefault(key, []).append(row)
    out: dict[_PeriodKey, PeriodRow] = {}
    for key in sorted(members):
        rows = members[key]
        if label is not None and len(rows) != len(frames):
            continue
        start = next((r[0] for r in rows if r[0] is not None), None)
        latest = rows[0]
        for row in rows[1:]:
            if (row[4], row[5]) >= (latest[4], latest[5]):
                latest = row
        tag = label if label is not None else "; ".join(r[6] for r in rows)
        out[key] = (
            start,
            key[0],
            key[1],
            kahan_sum([r[3] for r in rows]),
            max(r[4] for r in rows),
            latest[5],
            tag,
        )
    return out


def _alternative_rows(
    index: FactIndex, alt: Alternative, unit: str, instant: bool
) -> dict[_PeriodKey, PeriodRow]:
    frames = [index.tag_rows(tag, unit, instant=instant) for tag in alt]
    if len(frames) == 1:
        return frames[0]
    return _combine_rows(frames, " + ".join(_label(t) for t in alt))


def _part_rows(
    index: FactIndex, part: Part, unit: str, instant: bool
) -> dict[_PeriodKey, PeriodRow]:
    out: dict[_PeriodKey, PeriodRow] = {}
    for alt in part:  # the first alternative with a value for the period wins
        for key, row in _alternative_rows(index, alt, unit, instant).items():
            out.setdefault(key, row)
    return out


def resolve_rows(index: FactIndex, concept: Concept) -> list[PeriodRow]:
    """`resolve_concept` on a `FactIndex`, as plain rows sorted by period (end, then length)."""
    instant = concept.kind == "stock"
    parts = [_part_rows(index, part, concept.unit, instant) for part in concept.parts]
    parts = [p for p in parts if p]
    if not parts:
        return []
    found = parts[0] if len(parts) == 1 else _combine_rows(parts, None)
    return [found[key] for key in sorted(found)]


def periods_frame(rows: Sequence[PeriodRow]) -> pd.DataFrame:
    """Plain rows as a `PERIOD_COLUMNS` frame."""
    if not rows:
        return _empty_periods()
    starts, ends, days, values, stamps, accns, tags = zip(*rows, strict=True)
    return pd.DataFrame(
        {
            "period_start": pd.Series(list(starts), dtype="object"),
            "period_end": pd.Series(list(ends), dtype="object"),
            "duration_days": pd.Series(list(days), dtype="int64"),
            "value": pd.Series(list(values), dtype="float64"),
            "available_at": stamps_column(stamps),
            "accn": pd.Series(list(accns), dtype="str"),
            "tag": pd.Series(list(tags), dtype="str"),
        }
    )


def period_rows(periods: pd.DataFrame) -> list[PeriodRow]:
    """A `PERIOD_COLUMNS` frame as plain rows (in the frame's order)."""
    if periods.empty:
        return []
    starts = [None if _null(s) else s for s in periods["period_start"].tolist()]
    return list(
        zip(
            starts,
            periods["period_end"].tolist(),
            [int(d) for d in periods["duration_days"].tolist()],
            [float(v) for v in periods["value"].tolist()],
            stamps_us(periods["available_at"]),
            periods["accn"].tolist(),
            periods["tag"].tolist(),
            strict=True,
        )
    )


def resolve_concept(facts: pd.DataFrame, concept: Concept) -> pd.DataFrame:
    """The values of `concept` per period from `facts` (`fundamentals_facts` rows).

    Uses the facts in the concept's unit and of its kind (instants for a stock, durations for
    a flow or an average), the latest filing per tag and period (`latest_filings`), then per
    period the first
    alternative of each part that has a value, summed over the parts present. Returns
    `PERIOD_COLUMNS` sorted by period (end, then length); `tag` names the tag(s) used (`A + B`
    for a strict sum, `A; B` for parts), `available_at`/`accn` the latest input. A concept no
    tag reports gives an empty frame (not an error).
    """
    return periods_frame(resolve_rows(FactIndex(facts), concept))
