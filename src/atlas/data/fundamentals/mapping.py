"""Concept map: standardised fundamentals defined over XBRL tags (`config/xbrl_map.yaml`).

A concept (e.g. `revenue`) is a `flow` (a duration: summed into quarters and TTM) or a `stock`
(an instant: latest balance), in one XBRL `unit`. Its value for a period is the sum of its
**parts** that have a value (a missing part counts as 0; no part present → no value). A part is
a fallback list of **alternatives**, tried in order; an alternative is one tag or a strict sum
of tags (`"A + B"`, present only when every term has a value for that period). A concept
written with `tags:` has one part; one written with `sum_of:` has several.

Tags are `(taxonomy, name)` pairs: a bare name in the file is `us-gaap`, `dei:Name` is `dei`.
"""

import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Literal, Self

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

Kind = Literal["flow", "stock"]

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
