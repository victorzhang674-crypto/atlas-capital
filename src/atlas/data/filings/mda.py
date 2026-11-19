"""The MD&A section of a 10-K: HTML to text, then Item 7 of the filing, or the MD&A of the
annual-report exhibit (Exhibit 13) that Item 7 points to.

Headings are found on line starts of the text `html_to_text` produces (one block element per
line). A 10-K names Item 7 twice or more (table of contents, cross-references, the section
itself), so every candidate section is cut at the next heading that ends it and the longest
one is the MD&A. Sections shorter than `min_chars` are not an MD&A: either Item 7 points
elsewhere (`by_reference`) or nothing usable was found (`not_found`).
"""

import re
from dataclasses import dataclass
from typing import Literal

import lxml.html

MdaStatus = Literal["ok", "by_reference", "not_found"]
"""`ok`: the text is the MD&A. `by_reference`: Item 7 points to another document (Exhibit 13).
`not_found`: no section long enough."""


@dataclass(frozen=True)
class MdaExtract:
    """The outcome of an extraction: `status` and the MD&A `text` (`None` unless `ok`)."""

    status: MdaStatus
    text: str | None


_BLOCK_TAGS = frozenset(
    {"p", "div", "br", "tr", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6", "section"}
)
_CELL_TAGS = frozenset({"td", "th"})
_SPACES = re.compile(r"[ \t\r\f\v\u00a0\u2002\u2003\u2009]+")

_XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")
_SEP = r"[ \t]*[.:\-\u2013\u2014]?[ \t]*"
# A heading is "Item N" followed by nothing or by the item's own title: a cross-reference that
# starts a line ("Item 8 of this Form 10-K ...") is not one.
_ITEM_7 = re.compile(
    rf"^[ \t]*item[ \t]*7(?![0-9a-z]){_SEP}(?:$|management)", re.IGNORECASE | re.MULTILINE
)
_ITEM_7_END = re.compile(
    rf"^[ \t]*item[ \t]*(?:7a{_SEP}(?:$|quantitative)|8(?![0-9a-z]){_SEP}(?:$|financial|"
    r"consolidated))",
    re.IGNORECASE | re.MULTILINE,
)
_REFERENCE = re.compile(
    r"exhibit\s*13|incorporated\s+(?:herein\s+)?by\s+reference|annual\s+report\s+to\s+"
    r"(?:share|stock)holders",
    re.IGNORECASE,
)

_HEADING_MAX = 150
"""Longest line (characters) taken for a heading in an exhibit."""
_EXHIBIT_START = re.compile(
    r"^[^\n]{0,20}management(?:[\u2019'`]?s)?\s+discussion(?:\s+and\s+analysis)?[^\n]*$",
    re.IGNORECASE | re.MULTILINE,
)
_EXHIBIT_END = re.compile(
    r"^[ \t]*(?:report\s+of\s+independent\s+registered|consolidated\s+statements?\s+of|"
    r"consolidated\s+balance\s+sheets?|quantitative\s+and\s+qualitative\s+disclosures|"
    r"management[\u2019'`]?s\s+(?:annual\s+)?report\s+on\s+internal\s+control)[^\n]{0,120}$",
    re.IGNORECASE | re.MULTILINE,
)


def _decode(body: bytes) -> str:
    """EDGAR documents are UTF-8 (inline XBRL declares ASCII with character references) or,
    in older filings, Windows-1252, often without a declared charset."""
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


def html_to_text(html: bytes | str) -> str:
    """The visible text of an HTML (or inline XBRL) document: one line per block element,
    table cells of a row on one line, runs of spaces collapsed, blank lines dropped. Script,
    style and `display:none` elements (the inline XBRL header) are left out. Bytes are read
    as UTF-8, else as Windows-1252; an XML declaration is ignored."""
    source = _decode(html) if isinstance(html, bytes) else html
    source = _XML_DECLARATION.sub("", source, count=1)
    if not source.strip():
        return ""
    doc = lxml.html.fromstring(source)
    for gone in doc.xpath(
        "//script | //style | //*[contains(translate(@style, ' ', ''), 'display:none')]"
    ):
        gone.drop_tree()
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        tag = el.tag.lower()  # lxml.html keeps prefixes ("ix:header"), never "{ns}tag"
        if tag in _BLOCK_TAGS:
            el.tail = "\n" + (el.tail or "")
        elif tag in _CELL_TAGS:
            el.tail = " " + (el.tail or "")
    lines = (_SPACES.sub(" ", line).strip() for line in doc.text_content().split("\n"))
    return "\n".join(line for line in lines if line)


def _check(min_chars: int) -> None:
    if min_chars < 1:
        raise ValueError(f"min_chars must be at least 1, got {min_chars}")


def _sections(text: str, starts: re.Pattern[str], ends: re.Pattern[str]) -> list[str]:
    out = []
    for start in starts.finditer(text):
        end = ends.search(text, start.end())
        out.append(text[start.start() : end.start() if end else len(text)].strip())
    return out


def extract_mda(text: str, *, min_chars: int) -> MdaExtract:
    """The MD&A (Item 7) of a 10-K's text: from an `Item 7` heading to the next `Item 7A` or
    `Item 8` heading (or the end), the longest such section. A heading starts a line and is
    followed by nothing or by its item's title (Management's..., Quantitative..., Financial
    Statements... / Consolidated...). `ok` when it holds at least
    `min_chars` characters; else `by_reference` when it points to Exhibit 13 or the annual
    report, else `not_found`. Raises `ValueError` when `min_chars` < 1."""
    _check(min_chars)
    sections = _sections(text, _ITEM_7, _ITEM_7_END)
    if not sections:
        return MdaExtract("not_found", None)
    best = max(sections, key=len)
    if len(best) >= min_chars:
        return MdaExtract("ok", best)
    if any(_REFERENCE.search(s) for s in sections):
        return MdaExtract("by_reference", None)
    return MdaExtract("not_found", None)


def extract_exhibit_mda(text: str, *, min_chars: int) -> MdaExtract:
    """The MD&A of an annual-report exhibit (Exhibit 13): from a heading line (at most 150
    characters) naming "Management's Discussion and Analysis" (or IBM's "Management Discussion")
    to the next short line that starts
    a financial statement, the auditor's report, the market-risk disclosures or the internal-
    control report; the longest such section, `ok` when it holds at least `min_chars`
    characters, else `not_found`. Raises `ValueError` when `min_chars` < 1."""
    _check(min_chars)
    sections = [
        s
        for s in _sections(text, _EXHIBIT_START, _EXHIBIT_END)
        if len(s.split("\n", 1)[0]) <= _HEADING_MAX
    ]
    best = max(sections, key=len, default="")
    if len(best) >= min_chars:
        return MdaExtract("ok", best)
    return MdaExtract("not_found", None)
