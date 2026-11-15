"""Evidence for agents: `[E:<id>]` tags, the evidence an agent run knows, and the citation check.

Every piece of data an agent sees (pre-fetched or returned by a tool) is an `Evidence` with a
stable id from its source (`news:alpaca-1`, `fund:AAA:2023-12-31:revenue_ttm`, ...). The model
sees it as one line `[E:<id>] <published UTC> <text>` and cites it by that id.

The citation check (`check_citations`) walks a structured output: a *claim* is any nested
model with an `evidence_ids` field and must cite at least one id; *citations* are the ids in
`evidence_ids` fields, in an `evidence` field holding strings, and every inline `[E:<id>]` tag
in a string. Cited ids the run never showed the model are *unknown*. An output whose schema
can cite (it has a claim or an `evidence` field somewhere) but that cites nothing at all is
itself an uncited claim (path `<root>`).
"""

import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from typing import Any

from pydantic import BaseModel

from atlas.core.types.research import Evidence

EVIDENCE_ID_CHARS = r"A-Za-z0-9_.:/\-"
"""Characters an evidence id may hold (the tag syntax relies on it: no spaces, no `]`)."""

_ID_RE = re.compile(rf"^[{EVIDENCE_ID_CHARS}]{{1,200}}$")
TAG_RE = re.compile(rf"\[E:([{EVIDENCE_ID_CHARS}]{{1,200}})\]")
"""An inline citation `[E:<id>]` in model text."""

NO_DATA = "(no data)"
"""What a data block holds when there is no evidence."""

_SPACES = re.compile(r"\s+")


def check_evidence_id(evidence_id: str) -> str:
    """`evidence_id` unchanged if it can be written as a tag; `ValueError` otherwise."""
    if not _ID_RE.fullmatch(evidence_id):
        raise ValueError(
            f"evidence id {evidence_id!r} cannot be tagged: use 1-200 letters, digits and _ . : / -"
        )
    return evidence_id


def evidence_tag(evidence_id: str) -> str:
    """The inline citation of `evidence_id`: `[E:<id>]`."""
    return f"[E:{check_evidence_id(evidence_id)}]"


def evidence_line(item: Evidence) -> str:
    """One line for the model: `[E:<id>] <YYYY-MM-DD HH:MM>Z <text>`; whitespace in the text
    (newlines included) is collapsed to single spaces."""
    stamp = item.as_of.strftime("%Y-%m-%d %H:%M")
    return f"{evidence_tag(item.id)} {stamp}Z {_SPACES.sub(' ', item.text).strip()}"


def evidence_lines(items: Iterable[Evidence]) -> str:
    """`evidence_line` of each item, one per line; `(no data)` when there are none."""
    lines = [evidence_line(item) for item in items]
    return "\n".join(lines) if lines else NO_DATA


CLAIM_FIELD = "evidence_ids"
"""A nested model with this field is a claim and must cite at least one evidence id."""

LIST_FIELD = "evidence"
"""A field of this name holding strings lists cited evidence ids."""

CONFIDENCE_FIELDS = ("confidence", "conviction")
"""The top-level output fields `cap_confidence` caps (an analyst report's `confidence`, a
thesis's `conviction`)."""

ROOT = "<root>"
"""The path of the output itself in `CitationReport.uncited_claims`."""


class EvidenceLedger:
    """The evidence one agent run has shown the model, by id, in first-seen order. Adding an
    item whose id is known with other content raises `ValueError` (ids must be stable)."""

    def __init__(self, items: Iterable[Evidence] = ()) -> None:
        self._items: dict[str, Evidence] = {}
        self.add(items)

    def add(self, items: Iterable[Evidence]) -> None:
        """Record `items` (repeats of a known item are ignored)."""
        for item in items:
            known = self._items.get(item.id)
            if known is None:
                self._items[check_evidence_id(item.id)] = item
            elif known != item:
                raise ValueError(f"evidence id {item.id!r} was seen with different content")

    @property
    def ids(self) -> tuple[str, ...]:
        """Known ids in first-seen order."""
        return tuple(self._items)

    @property
    def items(self) -> tuple[Evidence, ...]:
        """Known items in first-seen order."""
        return tuple(self._items.values())

    def get(self, evidence_id: str) -> Evidence | None:
        """The item `evidence_id`, or `None`."""
        return self._items.get(evidence_id)

    def select(self, ids: Iterable[str]) -> tuple[Evidence, ...]:
        """The known items among `ids`, in that order (unknown ids are skipped)."""
        return tuple(self._items[i] for i in ids if i in self._items)

    def __contains__(self, evidence_id: object) -> bool:
        return evidence_id in self._items

    def __len__(self) -> int:
        return len(self._items)


@dataclass(frozen=True)
class CitationReport:
    """What an output cites: every `cited` id (first-seen order), the `unknown` ones among
    them, and the paths of `uncited_claims` (claims with an empty `evidence_ids`)."""

    cited: tuple[str, ...]
    unknown: tuple[str, ...]
    uncited_claims: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """No unknown id and no uncited claim."""
        return not self.unknown and not self.uncited_claims

    def describe(self) -> str:
        """The problems as short text for a repair message (`ok` when there are none)."""
        lines = []
        if self.unknown:
            lines.append(
                "Unknown evidence ids (not in the data or tool results): " + ", ".join(self.unknown)
            )
        if ROOT in self.uncited_claims:
            lines.append("The output cites no evidence at all.")
        claims = [path for path in self.uncited_claims if path != ROOT]
        if claims:
            lines.append("Claims that cite no evidence: " + ", ".join(claims))
        return "\n".join(lines) if lines else "ok"


def _child(path: str, part: str) -> str:
    return f"{path}.{part}" if path else part


def _is_strings(value: object) -> bool:
    return isinstance(value, (tuple, list)) and all(isinstance(v, str) for v in value)


def _schema_cites(node: object) -> bool:
    """Whether a JSON Schema (or part of one) has a property named like a citation field."""
    if isinstance(node, Mapping):
        props = node.get("properties")
        if isinstance(props, Mapping) and (CLAIM_FIELD in props or LIST_FIELD in props):
            return True
        return any(_schema_cites(value) for value in node.values())
    if isinstance(node, list):
        return any(_schema_cites(value) for value in node)
    return False


@cache
def can_cite(schema: type[BaseModel]) -> bool:
    """Whether outputs of `schema` can cite evidence (a claim or an `evidence` field anywhere
    in its JSON Schema)."""
    return _schema_cites(schema.model_json_schema())


def check_citations(output: BaseModel, known: Collection[str]) -> CitationReport:
    """Check the citations of `output` against the `known` evidence ids (see the module
    docstring for what counts as a claim and as a citation)."""
    cited: dict[str, None] = {}
    uncited: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, BaseModel):
            fields = type(value).model_fields
            if CLAIM_FIELD in fields and not getattr(value, CLAIM_FIELD):
                uncited.append(path or ROOT)
            for name in fields:
                item = getattr(value, name)
                if name in (CLAIM_FIELD, LIST_FIELD) and _is_strings(item):
                    cited.update(dict.fromkeys(item))
                else:
                    walk(item, _child(path, name))
        elif isinstance(value, str):
            cited.update(dict.fromkeys(TAG_RE.findall(value)))
        elif isinstance(value, Mapping):
            for key, item in value.items():
                walk(item, _child(path, str(key)))
        elif isinstance(value, (tuple, list)):
            for i, item in enumerate(value):
                walk(item, f"{path}[{i}]")

    walk(output, "")
    if not cited and ROOT not in uncited and can_cite(type(output)):
        uncited.insert(0, ROOT)
    return CitationReport(
        cited=tuple(cited),
        unknown=tuple(i for i in cited if i not in known),
        uncited_claims=tuple(uncited),
    )


def cap_confidence[M: BaseModel](output: M, cap: float) -> M:
    """`output` with each top-level `CONFIDENCE_FIELDS` value above `cap` lowered to `cap`;
    outputs with none of them, or with values at or below `cap`, are returned unchanged."""
    fields = type(output).model_fields
    update = {
        name: cap
        for name in CONFIDENCE_FIELDS
        if name in fields
        and isinstance(getattr(output, name), (int, float))
        and getattr(output, name) > cap
    }
    return output.model_copy(update=update) if update else output
