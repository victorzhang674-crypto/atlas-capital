"""Report checks shared by the research analysts (Stage 29, moved here in Stage 30).

They are deterministic, so their text can go into a repair turn and a recorded run replays:

- `sentences`: a text split into sentences;
- `uncited_numbers`: the sentences that state a number (digits outside evidence tags and ISO
  dates) without an `[E:<id>]` tag;
- `ungrounded_numbers`: the tagged sentences with a number that none of the evidence items they
  cite states (a tag covers only the numbers its own evidence holds);
- `missing_sections`: the labelled summary sections absent or out of order;
- `quote`: at most `MAX_LISTED` offending sentences, each cut at 120 characters.
"""

import re
from collections.abc import Mapping, Sequence
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from functools import cache

from atlas.agents.evidence import TAG_RE

MAX_LISTED = 5
"""A problem lists at most this many offending sentences."""

QUOTE_CHARS = 120
"""A quoted sentence is cut at this many characters."""

_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_DIGIT = re.compile(r"\d")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


@cache
def _section_re(label: str) -> re.Pattern[str]:
    return re.compile(rf"(?:^|\n)[ \t*#_>-]*{re.escape(label)}[ \t*_]*:", re.IGNORECASE)


def sentences(text: str) -> list[str]:
    """`text` split into sentences (after `.`, `!`, `?` and at line breaks); a piece holding
    only evidence tags is joined to the sentence before it."""
    out: list[str] = []
    for piece in _SENTENCE_END.split(text):
        piece = piece.strip()
        if not piece:
            continue
        if out and not TAG_RE.sub("", piece).strip(" .;,"):
            out[-1] = f"{out[-1]} {piece}"
        else:
            out.append(piece)
    return out


def uncited_numbers(text: str) -> list[str]:
    """The sentences of `text` that state a number (digits outside evidence tags and ISO
    dates) without an `[E:<id>]` tag."""
    return [
        s
        for s in sentences(text)
        if _DIGIT.search(_ISO_DATE.sub("", TAG_RE.sub("", s))) and not TAG_RE.search(s)
    ]


def _numbers(text: str) -> list[Decimal]:
    """The unsigned numbers written in `text` (`1,234.5` is 1234.5), with the digits they were
    written with (`2.00` keeps two places)."""
    return [Decimal(token.replace(",", "")) for token in _NUMBER.findall(text)]


def _year(date_match: re.Match[str]) -> str:
    """The year of an ISO date: the only part of a date an evidence item is taken to state
    (`FY2023`), not its month or day, which would match any small number."""
    return date_match.group()[:4]


def _grounded(number: Decimal, pool: Sequence[Decimal]) -> bool:
    """Whether `pool` holds `number`: the same value, or a value that rounds (or cuts) to it at
    the places `number` was written with (a model may round the evidence further)."""
    places = max(0, -int(number.as_tuple().exponent))
    step = Decimal(1).scaleb(-places)
    for value in pool:
        if value == number:
            return True
        try:
            if number in (
                value.quantize(step, rounding=ROUND_HALF_UP),
                value.quantize(step, rounding=ROUND_DOWN),
            ):
                return True
        except InvalidOperation:  # a value too large to round at these places
            continue
    return False


def ungrounded_numbers(text: str, evidence: Mapping[str, str]) -> list[str]:
    """The sentences of `text` that cite known evidence (`evidence`: id to text) yet state a
    number (digits outside evidence tags and ISO dates, signs ignored) that none of the items
    they cite holds. A tag covers only the numbers its own item states, so one tag cannot stand
    for every figure of a sentence; several tags in a sentence cover the sum of their items.
    A number matches an item's number of the same value, or one that rounds or cuts to it at
    the places written; the year of an ISO date in the evidence counts as a number it holds (not
    its month or day). Sentences with no digit, no tag or only unknown tags are left to
    `uncited_numbers` and the citation check."""
    out = []
    for sentence in sentences(text):
        known = [t for t in dict.fromkeys(TAG_RE.findall(sentence)) if t in evidence]
        if not known:
            continue
        stated = _numbers(_ISO_DATE.sub("", TAG_RE.sub("", sentence)))
        pool = [n for t in known for n in _numbers(_ISO_DATE.sub(_year, evidence[t]))]
        if any(not _grounded(n, pool) for n in stated):
            out.append(sentence)
    return out


def grounding_problems(
    summary: str,
    risks: Sequence[str],
    evidence: Mapping[str, str],
    others: Mapping[str, Sequence[str]] | None = None,
) -> list[str]:
    """The `ungrounded_numbers` problem of a report (its `summary`, each of its `risks` and
    each text of `others`, a label such as `catalysts` to the texts under it) as the
    repair-turn text, or no problem."""
    bad = ungrounded_numbers(summary, evidence)
    labelled = {"risks": risks, **(others or {})}
    for label, texts in labelled.items():
        for i, text in enumerate(texts):
            bad += [f"{label}[{i}]: {s}" for s in ungrounded_numbers(text, evidence)]
    if not bad:
        return []
    return [
        "every number must be stated by an evidence item cited in the same sentence: put the "
        "[E:<id>] of the item that states each number in that sentence (several tags are fine), "
        "or drop the number. These sentences state a number that the evidence they cite does "
        f"not hold: {quote(bad)}"
    ]


def missing_sections(summary: str, sections: Sequence[str]) -> list[str]:
    """The `sections` labels absent from `summary`, or out of order (each must start a line,
    optionally after markdown marks, end with a colon and come after the one before; case is
    ignored)."""
    missing, position = [], 0
    for label in sections:
        found = _section_re(label).search(summary, position)
        if found is None:
            missing.append(label)
        else:
            position = found.end()
    return missing


def quote(sentences_: Sequence[str]) -> str:
    """At most `MAX_LISTED` of `sentences_`, quoted and cut at `QUOTE_CHARS` characters,
    joined by "; ", with " and N more" when some are left out."""
    shown = "; ".join(f'"{s[:QUOTE_CHARS]}"' for s in sentences_[:MAX_LISTED])
    more = f" and {len(sentences_) - MAX_LISTED} more" if len(sentences_) > MAX_LISTED else ""
    return shown + more


__all__ = [
    "MAX_LISTED",
    "QUOTE_CHARS",
    "grounding_problems",
    "missing_sections",
    "quote",
    "sentences",
    "uncited_numbers",
    "ungrounded_numbers",
]
