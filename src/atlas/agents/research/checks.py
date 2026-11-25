"""Report checks shared by the research analysts (Stage 29, moved here in Stage 30).

They are deterministic, so their text can go into a repair turn and a recorded run replays:

- `sentences`: a text split into sentences;
- `uncited_numbers`: the sentences that state a number (digits outside evidence tags and ISO
  dates) without an `[E:<id>]` tag;
- `missing_sections`: the labelled summary sections absent or out of order;
- `quote`: at most `MAX_LISTED` offending sentences, each cut at 120 characters.
"""

import re
from collections.abc import Sequence
from functools import cache

from atlas.agents.evidence import TAG_RE

MAX_LISTED = 5
"""A problem lists at most this many offending sentences."""

QUOTE_CHARS = 120
"""A quoted sentence is cut at this many characters."""

_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_DIGIT = re.compile(r"\d")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


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


__all__ = ["MAX_LISTED", "QUOTE_CHARS", "missing_sections", "quote", "sentences", "uncited_numbers"]
