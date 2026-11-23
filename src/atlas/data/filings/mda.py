"""The MD&A section of a 10-K or 10-Q: HTML to text, then Item 7 of a 10-K (Part I Item 2 of
a 10-Q), or the MD&A of the annual report (Exhibit 13 or 99, or a later section of the same
document) that Item 7 points to.

Headings are found on line starts of the text `html_to_text` produces (one block element per
line). A 10-K names Item 7 twice or more (table of contents, cross-references, the section
itself), so every candidate section is cut at the next heading that ends it and the longest
one is the MD&A. Sections shorter than `min_chars` are not an MD&A: either Item 7 points
elsewhere (`by_reference`) or nothing usable was found (`not_found`).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import lxml.html

MdaStatus = Literal["ok", "by_reference", "not_found"]
"""`ok`: the text is the MD&A. `by_reference`: the MD&A item points elsewhere (an exhibit).
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
# A slash or a bar may separate an item from its title (AIG: "ITEM 2 / MANAGEMENT'S ...",
# "ITEM 6 | Exhibits").
_SEP = r"[ \t]*[.:/|\-\u2013\u2014]?[ \t]*"
# "ITEM" may stand on a line of its own, its number starting the next (SolarEdge 2024, Kimco).
# An end heading is singular: "Special" / "Items 3" heads a table column (Mastercard).
_ITEM = r"item[ \t]*(?:\n[ \t]*)?"
# The end of a heading line that is no contents entry: the line does not end in a page number
# and no bare page number ("101", "F-6") stands on the next line (GE's and Citi's 10-Q MD&As
# open with contents listing "Signatures 101", "Exhibit Index 220").
_ENTRY_END = r"(?<![0-9])[ \t]*$(?!\n[ \t]*(?:[a-z][ \t]*[-\u2013][ \t]*)?\d{1,4}[ \t]*$)"
# A heading naming its own place ("... of this report", "... in our Annual Report") is a
# cross-reference.
_NOT_A_REFERENCE = (
    r"(?![^\n]*\b(?:of|in)[ \t]+(?:this|our|the)\b[^\n]{0,20}?\b(?:report|annual|quarterly|form))"
)
# What follows every MD&A: the signatures and the exhibit index (Expedia, Molina, Eaton).
_AFTER_ALL = rf"(?:signatures?|exhibit[ \t]+index|index[ \t]+to[ \t]+exhibits)[ \t.:]*{_ENTRY_END}"
# A heading is "Item N" followed by nothing or by the item's own title: a cross-reference that
# starts a line ("Item 8 of this Form 10-K ...") is not one. "Managment" and "Mangement" are
# filers' misspellings (Roper 2017, Gardner Denver 2018-19).
_MANAGEMENT = r"mana?ge?ment"
# A 10-K item may carry its part ("Part II. Item 7.", Expedia).
_PART = r"(?:part[ \t]+ii[ \t]*[.,:\-\u2013\u2014]?[ \t]*)?"
# "Items 7. and 7A." heads a combined section (Freeport-McMoRan).
_ITEM_7 = re.compile(
    rf"^[ \t]*{_PART}items?[ \t]*(?:\n[ \t]*)?7(?![0-9a-z])"
    rf"(?:[ \t]*\.?[ \t]*and[ \t]*7a(?![0-9a-z]))?{_SEP}(?:$|{_MANAGEMENT})",
    re.IGNORECASE | re.MULTILINE,
)
# The auditor's report also ends an item: Hartford's 10-K has no "Item 8" heading after its
# MD&A, PepsiCo's 10-Q puts its statements and the review report after it. A line followed by a
# bare page number is an entry of the MD&A's own contents (PepsiCo's 10-Ks, 2015-2020).
_AUDITOR = (
    r"report[ \t]+of[ \t]+independent[ \t]+registered[ \t]+public[ \t]+accounting[ \t]+firm[ \t]*$"
    r"(?!\n[ \t]*\d{1,4}[ \t]*$)"
)
# A later part of the 10-K also ends Item 7 when the filer prints no Item 7A or 8 heading after
# it (Truist and Hartford: Item 9A; Part III, the signatures, the exhibit index), titled and not
# a contents entry or a cross-reference. ("Index to Financial Statements" is no end: Waste
# Management prints it in every page header.)
_LATER_10K = (
    rf"{_ITEM}(?:9[abc]?|1[0-6])(?![0-9a-z]){_SEP}(?:\n[ \t]*)?{_NOT_A_REFERENCE}(?:changes|"
    rf"controls|other|disclosure|directors|executive|security|certain|principal|exhibits?|form)"
    rf"[^\n]{{0,120}}{_ENTRY_END}"
    rf"|part[ \t]+(?:iii|iv)[ \t.:]*{_ENTRY_END}"
)
_ITEM_7_END = re.compile(
    rf"^[ \t]*(?:{_PART}{_ITEM}(?:7a{_SEP}(?:$|quantitative)|8(?![0-9a-z]){_SEP}(?:$|financial|"
    rf"consolidated))|{_PART}(?:{_LATER_10K})|{_AFTER_ALL}|{_AUDITOR})",
    re.IGNORECASE | re.MULTILINE,
)
# A 10-Q's Part II Item 2 (Unregistered Sales ...) is also "Item 2": a bare heading is the MD&A's
# only when "Management..." follows on the next line. Banks combine it with Item 3 ("Items 2.
# and 3.", Bank of New York Mellon, Northern Trust); "ITEM" may stand on a line of its own
# (A. O. Smith); a "Part I," may come first (Computer Sciences).
_ITEM_2 = re.compile(
    rf"^[ \t]*(?:part[ \t]+i[ \t]*[.,:\-\u2013\u2014]?[ \t]*)?items?[ \t]*(?:\n[ \t]*)?"
    rf"2(?![0-9a-z])(?:[ \t]*\.?[ \t]*and[ \t]*3(?![0-9a-z]))?"
    rf"{_SEP}(?:\n[ \t]*)?{_MANAGEMENT}",
    re.IGNORECASE | re.MULTILINE,
)
# Item 1 (the statements) ends the MD&A of a bank that files it after the MD&A; its title must
# follow on its line or the next ("Item 1." / "Business ..." is a cross-reference, Everest).
# A "Part II" line citing the annual report ("Part II, Item 7. MD&A ... in the 2015 Annual
# Report", AIG) is no end. Part II's items end it too when no "Part II" line is seen (Computer
# Sciences, AIG, Kimco, Garmin, Masco, Entergy), titled and not a contents entry.
_PART_II_10Q = (
    rf"{_ITEM}(?:1(?![0-9a-z]){_SEP}(?:\n[ \t]*)?legal[ \t]+proceedings|1a{_SEP}(?:\n[ \t]*)?"
    rf"risk[ \t]+factors|2(?![0-9a-z]){_SEP}(?:\n[ \t]*)?unregistered[^\n]{{0,100}}|"
    rf"3(?![0-9a-z]){_SEP}(?:\n[ \t]*)?defaults?[ \t]+upon[ \t]+senior[ \t]+securities|"
    rf"4(?![0-9a-z]){_SEP}(?:\n[ \t]*)?(?:mine[ \t]+safety[ \t]+disclosures?|"
    rf"submission[^\n]{{0,100}})|"
    rf"5(?![0-9a-z]){_SEP}(?:\n[ \t]*)?other[ \t]+information|6(?![0-9a-z]){_SEP}(?:\n[ \t]*)?"
    rf"exhibits?)[ \t.:]*{_ENTRY_END}"
)
_ITEM_2_END = re.compile(
    rf"^[ \t]*(?:{_ITEM}(?:1(?![0-9a-z]){_SEP}(?:\n[ \t]*)?(?:financial|consolidated|condensed)|"
    rf"3(?![0-9a-z]){_SEP}(?:$|quantitative)|4(?![0-9a-z]){_SEP}(?:$|controls))|"
    rf"part[ \t]+ii(?![a-z]){_NOT_A_REFERENCE}|{_PART_II_10Q}|{_AFTER_ALL}|{_AUDITOR})",
    re.IGNORECASE | re.MULTILINE,
)
_ITEMS = {"10-K": (_ITEM_7, _ITEM_7_END), "10-Q": (_ITEM_2, _ITEM_2_END)}
"""The MD&A item of each base form: its heading and the headings that end it."""
_ITEM_LINE = re.compile(r"^[ \t]*item[ \t]*\d", re.IGNORECASE)
_REFERENCE = re.compile(
    r"exhibit\s*13|incorporated\s+(?:(?:herein|in(?:to)?\s+this\s+(?:report|document))\s+)?by\s+"
    r"reference|annual\s+report\s+to\s+"
    r"(?:share|stock)holders",
    re.IGNORECASE,
)

_BULLETS = frozenset("\u2022\u25cf\u25aa\u00b7")
"""Characters that open a list entry in an EDGAR document's text."""
_NOT_A_HEADING = re.compile(r"^\s*(?:\d+[a-z]?\s|[\"'\u201c\u2018]|see\b)", re.IGNORECASE)
"""A line naming the MD&A that starts with a number and a space (a table-of-contents entry,
"7 Management's ..."), a quote or "See" (a cross-reference) is not its heading; a page number
glued to the heading ("18Management Discussion", IBM) does not count."""
_PAGE_END = re.compile(r"\d[ \t]*$", re.MULTILINE)
"""A line ending in a number: a table-of-contents entry with its page."""
_HEADING_MAX = 150
"""Longest line (characters) taken for a heading in an exhibit."""
_RUN_IN = re.compile(
    r"\([\"'\u201c\u2018]?MD&A[\"'\u201d\u2019]?\)|,\s+or\s+[\"'\u201c\u2018]?MD&A[\"'\u201d\u2019]?,"
)
"""The mark of a heading run into its first paragraph ("... OF OPERATIONS (MD&A). The ...",
GE; "The following is management's discussion and analysis ("MD&A") ...", JPMorgan;
"... Results of Operations, or MD&A, provides ...", Avery Dennison)."""
_RUN_IN_OPENS = re.compile(
    r"[ \t]*(?:the[ \t]+following[ \t]+is[ \t]+)?mana?ge?ment", re.IGNORECASE
)
"""How a run-in heading opens; "In our Management's Discussion ... (MD&A) section you will
find ..." (Weyerhaeuser's Item 1) is a sentence about the MD&A, not its start."""
_QUOTE_MARKS = frozenset('\u201d"')
"""A closing quote on an end line makes it a cross-reference ('... "Part I." / "Item 1.
Financial Statements" of this filing.', Blackstone, Devon)."""
_LINK = re.compile(
    r"[ \t]*item[ \t]*\d+[a-z]?(?![0-9a-z])[ \t]*[.:\-\u2013\u2014]?[ \t]*[a-z][^\n]*?"
    r"\s[\u2013\u2014-]\s",
    re.IGNORECASE,
)
"""An "Item" line naming a part of the item after a dash ("Item 8. Financial Statements and
Supplementary Data \u2013 Note 5. ...", Noble Energy): a link, not a heading."""
_OPEN_WORD = re.compile(
    r"(?:.{40,}\s|)(?:a|an|the|of|in|on|at|to|for|from|by|with|under|see|and|or|our|this|its|"
    r"their)$|.*,$",
    re.IGNORECASE,
)
"""A line of at least 40 characters breaking after a word that cannot end a sentence, a line
breaking after a comma, or a line that is just such a word (a word per line, Everest's 2023
10-K): the line after it goes on with the sentence ("... included under" / "Item 8.", Loews;
"... Retirement and Part II," / "Item 1A. Risk Factors.", AIG; "under" / "ITEM" / "8" / "of",
Everest)."""
_EXHIBIT_START = re.compile(
    r"^(?:[^\n]{0,20}management(?:[\u2019'`]?s)?\s+(?:financial\s+)?discussion"
    r"(?:\s+and\s+analysis)?[^\n]*"
    # an upper-case heading may name the company first (AEP's combined 10-K); Entergy's says
    # "MANAGEMENT'S FINANCIAL DISCUSSION AND ANALYSIS"
    r"|(?-i:[^\na-z]{0,100}MANAGEMENT[\u2019'`]?S\s+(?:FINANCIAL\s+)?DISCUSSION\s+AND\s+"
    r"ANALYSIS[^\n]*)"
    # Wells Fargo's annual report, with an optional footnote mark
    r"|[ \t]*financial[ \t]+review[ \t]*\d?[ \t]*)$",
    re.IGNORECASE | re.MULTILINE,
)
# A line on a statement that ends in a word such as "Information", "Analysis" or "Overview" is
# a section of the MD&A (Synchrony's "Consolidated Statements of Earnings Information",
# JPMorgan's "Consolidated Balance Sheets overview", PNC's "Consolidated Balance Sheet Review");
# an upper-case "FINANCIAL STATEMENTS" line starts GE's statements, and "Report of Management"
# U.S. Bancorp's reports after its MD&A.
_EXHIBIT_END = re.compile(
    r"^[ \t]*(?![^\n]*(?:information|analysis|overview|review|discussion|data|summary|highlights)"
    # an item number may come first ("Item 3. Quantitative ...": Axon's misnumbered 10-Q MD&A)
    r"[ \t]*$)(?:item[ \t]*\d+[a-z]?[ \t]*[.:\-\u2013\u2014]?[ \t]*)?"
    r"(?:(?-i:FINANCIAL[ \t]+STATEMENTS)(?=[ \t]*$)|report\s+of\s+management[ \t]*$|"
    r"report\s+of\s+independent\s+registered|(?:condensed\s+)?consolidated\s+"
    r"statements?\s+of|(?:condensed\s+)?consolidated\s+balance\s+sheets?|quantitative\s+and\s+qualitative\s+disclosures?|"
    r"management[\u2019'`]?s\s+(?:annual\s+)?report\s+on\s+internal\s+control)[^\n]{0,120}$"
    # the signatures, the exhibit index, a 10-Q's "Part II - Other Information" line and its titled
    # Part II items (follow-up 5)
    rf"|^[ \t]*(?:{_AFTER_ALL}|{_PART_II_10Q}|{_ITEM}\d+{_SEP}exhibits?[ \t.:]*{_ENTRY_END}|"
    rf"part[ \t]+ii{_SEP}other[ \t]+information[ \t.:]*{_ENTRY_END})",
    re.IGNORECASE | re.MULTILINE,
)
_POINTER = re.compile(
    r"reference\s+is\s+made\s+to|set\s+forth\s+(?:in|under)|sections?\s+(?:entitled|captioned|headed)",
    re.IGNORECASE,
)
"""More wordings of an item pointing to named sections elsewhere (Comerica, Emerson)."""
_QUOTED = re.compile(r"[\u201c\"]([^\u201c\u201d\"\n]*)[\u201d\"]")
"""A double-quoted string (curly or straight quotes, mixed as filers mix them)."""
_LINE = re.compile(r"^[^\n]{1,150}$", re.MULTILINE)
"""A line short enough to be a heading."""
_CONTENTS_PAGE = re.compile(
    r"[ \t]*(?:[a-z][ \t]*[-\u2013][ \t]*)?\d{1,4}[ \t]*$"
    r"|\n[ \t]*(?:[a-z][ \t]*[-\u2013][ \t]*)?\d{1,4}[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
"""What follows a contents entry: its page ("6", "F-6", "F - 6") at the end of its line or on
the next line."""
_NEXT_PAGE = re.compile(
    r"[^\n]*\n[ \t]*(?:[a-z][ \t]*[-\u2013][ \t]*)?\d{1,4}[ \t]*(?:\n|$)", re.IGNORECASE
)
"""A heading line followed by a bare page number: a contents entry (Entergy's 10-Q contents,
"Management\u2019s Financial Discussion and Analysis" / "169")."""


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


def _not_an_end(text: str, at: int) -> bool:
    """Whether an end heading matched at `at` (a line start) is no heading: an entry of a list
    (the line before is a bullet or starts with one), a link to part of an item, a line going
    on with the sentence of the line before, or a line holding a closing quote (a quoted
    cross-reference)."""
    before = text[: max(at - 1, 0)]
    previous = before[before.rfind("\n") + 1 :].strip()
    stop = text.find("\n", at)
    line = text[at : stop if stop >= 0 else len(text)]
    return (
        previous[:1] in _BULLETS
        or _LINK.match(text, at) is not None
        or _continues(text, at)
        or not _QUOTE_MARKS.isdisjoint(line)
    )


def _continues(text: str, at: int) -> bool:
    """Whether the line starting at `at` goes on with the sentence of the line before it (that
    line breaks after a word that cannot end a sentence, or after a comma)."""
    before = text[: max(at - 1, 0)]
    return _OPEN_WORD.fullmatch(before[before.rfind("\n") + 1 :].strip()) is not None


def _sections(text: str, starts: re.Pattern[str], ends: re.Pattern[str]) -> list[str]:
    """Every section from a `starts` match to the next `ends` match (or the end of `text`)
    that is a heading (`_not_an_end`) and not the line right under a start line without a
    page number (a combined heading)."""
    out = []
    for start in starts.finditer(text):
        if _continues(text, start.start()):
            continue  # a heading never goes on with the sentence before it (Morgan Stanley)
        # an end heading on the line right under the start heading is part of a combined
        # heading ("Item 7. ..." / "Item 7A. ...", Regions), unless the start line ends in a
        # page number (a table of contents)
        line_end = text.find("\n", start.end())
        combined = line_end >= 0 and not _PAGE_END.search(text, start.start(), line_end)
        after = text.find("\n", line_end + 1) if combined else -1
        end = ends.search(text, start.end())
        while end is not None and (
            _not_an_end(text, end.start()) or line_end < end.start() <= after
        ):
            end = ends.search(text, end.end())
        out.append(text[start.start() : end.start() if end else len(text)].strip())
    return out


def points_elsewhere(text: str) -> bool:
    """Whether `text` points to Exhibit 13, says it is incorporated by reference, or names the
    annual report to shareholders."""
    return _REFERENCE.search(text) is not None


def extract_mda(text: str, *, min_chars: int, form: str = "10-K") -> MdaExtract:
    """The MD&A of a filing's text. A 10-K's (and its variants', such as 10-K405) is Item 7:
    from an `Item 7` (or `Items 7 and 7A`) heading to the next `Item 7A` or `Item 8` heading,
    a later item's heading (9, 9A-9C, 10-16) or `Part III`/`Part IV` (or the end). A 10-Q's
    (`form` starting with 10-Q) is Part I Item 2: from an `Item 2` (or `Items 2 and 3`) heading
    followed by "Management..." on its line or the next (not Part II's "Item 2. Unregistered
    Sales ...") to the next `Item 1` (statements after the MD&A, titled on its line or the
    next), `Item 3`, `Item 4` or `Part II` heading (not one citing the annual report), or one
    of Part II's titled items (Legal Proceedings ... Exhibits). Either ends at the signatures,
    the exhibit index or the auditor's report. The longest such section is taken. A heading
    starts a line ("ITEM" may stand on a line of its own) and is followed by nothing or by its
    item's title (Management's..., Quantitative..., Financial Statements... / Consolidated...,
    Controls...), after an optional ".", ":", "/", "|" or dash. An end heading that is an entry
    of a bulleted list or of a table of contents (a page number at its end or on the next
    line, for the later items, the signatures and the exhibit index), a link to part of an item
    ("Item 8. ... \u2013 Note 5"), a line going on with the sentence before it (which breaks
    after "under", "the", ..., or a comma) or a line holding a closing quote does not end the
    section. `ok` when it holds at least `min_chars` characters; else `by_reference` when it
    points to an exhibit or the annual report, else `not_found`. Raises `ValueError` when
    `min_chars` < 1."""
    _check(min_chars)
    starts, ends = _ITEMS["10-Q" if form.strip().upper().startswith("10-Q") else "10-K"]
    sections = _sections(text, starts, ends)
    if not sections:
        return MdaExtract("not_found", None)
    best = max(sections, key=len)
    if len(best) >= min_chars:
        return MdaExtract("ok", best)
    if any(points_elsewhere(s) for s in sections):
        return MdaExtract("by_reference", None)
    return MdaExtract("not_found", None)


def item_sections(text: str, form: str = "10-K") -> list[str]:
    """Every section of `text` under the MD&A item heading of `form` (10-K Item 7, 10-Q Part I
    Item 2), cut as in `extract_mda`, in document order: the contents entry, cross-references
    and the item itself."""
    starts, ends = _ITEMS["10-Q" if form.strip().upper().startswith("10-Q") else "10-K"]
    return _sections(text, starts, ends)


def named_sections(text: str) -> tuple[str, ...]:
    """The section titles an MD&A item names: the quoted strings (3-100 characters, closing
    punctuation dropped, each once) on the lines of `text` that point elsewhere ("incorporated
    ... by reference", Exhibit 13, "Reference is made to", "set forth in/under", "the sections
    entitled"). Emerson's FY2015 Item 7 names "Results of Operations", "Business Segments", ...
    of its Exhibit 13; Comerica's Item 7 names the sections of its 10-K's Financial Section."""
    titles: dict[str, None] = {}
    for line in text.split("\n"):
        if _REFERENCE.search(line) is None and _POINTER.search(line) is None:
            continue
        for quoted in _QUOTED.findall(line):
            title = _SPACES.sub(" ", quoted).strip().rstrip(",.;:").strip()
            if 3 <= len(title) <= 100:
                titles.setdefault(title)
    return tuple(titles)


def _title_key(line: str) -> str:
    """A line as a named section's heading: quotes, closing punctuation and case dropped,
    spaces collapsed."""
    return (
        _SPACES.sub(" ", line.strip(" \t\"'\u201c\u201d\u2018\u2019").rstrip(".:,;"))
        .strip()
        .lower()
    )


def extract_titled_mda(text: str, titles: Sequence[str], *, min_chars: int) -> MdaExtract:
    """The MD&A made of the sections an item names (`named_sections`): from a line that is one
    of `titles` (whatever its case, quotes and closing punctuation), not a contents entry (a
    page number at its end or on the next line: "6", "F-6") nor itself an end heading (the
    market-risk section an item may name too, Clorox), to the next annual-report end
    heading (`extract_exhibit_mda`'s: a statement, the auditor's report, ...). The longest such
    section is `ok` when it holds at least `min_chars` characters, else `not_found` (also with
    no titles). Emerson's FY2015 Exhibit 13 opens with management's reports and gives its MD&A
    sections no MD&A heading. Raises `ValueError` when `min_chars` < 1."""
    _check(min_chars)
    wanted = {_title_key(t) for t in titles}
    sections = []
    for line in _LINE.finditer(text):
        if (
            _title_key(line.group(0)) not in wanted
            or _CONTENTS_PAGE.match(text, line.end())
            or _EXHIBIT_END.match(text, line.start())  # a title that ends an MD&A (Clorox)
        ):
            continue
        end = _EXHIBIT_END.search(text, line.end())
        while end is not None and _not_an_end(text, end.start()):
            end = _EXHIBIT_END.search(text, end.end())
        sections.append(text[line.start() : end.start() if end else len(text)].strip())
    best = max(sections, key=len, default="")
    if len(best) >= min_chars:
        return MdaExtract("ok", best)
    return MdaExtract("not_found", None)


def extract_exhibit_mda(text: str, *, min_chars: int, skip_items: bool = False) -> MdaExtract:
    """The MD&A of an annual report (Exhibit 13 or 99, or a section of the 10-K itself): from a
    heading line (at most 150 characters) naming "Management's Discussion and Analysis" (or
    IBM's "Management Discussion", Entergy's "Management's Financial Discussion and
    Analysis"), or a line that is just "Financial Review" (Wells Fargo), to the next short line
    that starts a financial statement (condensed or not; not a line ending in "Information",
    "Analysis", "Overview", "Review", ...: a section of the MD&A), the auditor's report, the
    market-risk disclosures, the internal-control report, "Report of Management" or an
    upper-case "FINANCIAL STATEMENTS" line, each possibly after an "Item <n>" number (not an
    end that is no heading, as in `extract_mda`), or the signatures, the exhibit index or a
    titled Part II item of a 10-Q ("Item 5. Other Information", ...) or an "Item <n>.
    Exhibits" heading. A longer line opening with "Management..." or "The following
    is management..." is a heading when a run-in "(MD&A)" or ", or MD&A," mark sits in its
    first 150 characters. A line starting with a number or followed by a bare page number (a
    table of contents), a quote or "See" (a cross-reference) is not a heading. The longest such
    section is `ok` when it holds at least `min_chars` characters, else `not_found`. With
    `skip_items`, a heading line that starts with "Item <n>" (the 10-K item pointing to the
    section) is not a start. Raises `ValueError` when `min_chars` < 1."""
    _check(min_chars)
    sections = [
        s
        for s in _sections(text, _EXHIBIT_START, _EXHIBIT_END)
        if (
            len(first := s.split("\n", 1)[0]) <= _HEADING_MAX
            or (
                _RUN_IN.search(first[:_HEADING_MAX]) is not None
                and _RUN_IN_OPENS.match(first) is not None
            )
        )
        and not _NOT_A_HEADING.match(first)
        and not _NEXT_PAGE.match(s)
        and not (skip_items and _ITEM_LINE.match(first))
    ]
    best = max(sections, key=len, default="")
    if len(best) >= min_chars:
        return MdaExtract("ok", best)
    return MdaExtract("not_found", None)
