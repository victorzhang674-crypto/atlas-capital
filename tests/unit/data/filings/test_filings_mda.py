"""Stage 29 follow-up 2 (F2): 10-K HTML to text, and the MD&A section of a 10-K (Item 7) or
of the annual-report exhibit (Exhibit 13) that Item 7 points to."""

import pytest

from atlas.data.filings.mda import MdaExtract, extract_exhibit_mda, extract_mda, html_to_text

BODY = "Revenue grew because demand was strong. " * 40  # about 1,600 characters


def ten_k(
    item7: str, *, toc: bool = True, end: str = "Item 7A. Quantitative and Qualitative"
) -> str:
    """A small 10-K text: a table of contents, Item 1, Item 7 and the item after it."""
    contents = (
        "Table of Contents\nItem 1. Business 3\n"
        "Item 7. Management\u2019s Discussion and Analysis 20\n"
        "Item 7A. Quantitative and Qualitative Disclosures 30\nItem 8. Financial Statements 31\n"
        if toc
        else ""
    )
    return (
        f"{contents}Item 1. Business\nWe make things.\n"
        f"Item 7. Management\u2019s Discussion and Analysis of Financial Condition\n{item7}\n"
        f"{end} Disclosures About Market Risk\nRates moved.\nItem 8. Financial Statements\n"
    )


# --- html_to_text -------------------------------------------------------------------------------


def test_html_to_text_keeps_block_breaks_and_drops_hidden_and_script_text() -> None:
    html = (
        b"<html><head><style>p {color: red}</style><script>var x = 1;</script></head><body>"
        b'<div style="display: none"><ix:header>hidden facts</ix:header></div>'
        b"<p>Item&#160;7.&nbsp;Management&#8217;s Discussion</p><p>First   line</p>"
        b"<table><tr><td>Revenue</td><td>$ 10</td></tr></table><div>Last<br/>line</div>"
        b"</body></html>"
    )
    text = html_to_text(html)
    assert text.split("\n") == [
        "Item 7. Management\u2019s Discussion",
        "First line",
        "Revenue $ 10",
        "Last",
        "line",
    ]


@pytest.mark.parametrize(
    "body",
    [
        "<p>Management\u2019s Discussion</p>".encode(),  # UTF-8, no charset declared
        "<p>Management\u2019s Discussion</p>".encode("cp1252"),  # older filings: Windows-1252
        b"<?xml version='1.0' encoding='ASCII'?><html><body><p>Management&#8217;s Discussion</p>"
        b"</body></html>",  # inline XBRL
    ],
)
def test_html_to_text_decodes_edgar_documents(body: bytes) -> None:
    assert html_to_text(body) == "Management\u2019s Discussion"


def test_html_to_text_takes_text_too_and_skips_comments() -> None:
    html = "<!-- a comment --><p>One</p><?pi x?><P>Two</P><div>Three</div>"
    assert html_to_text(html) == "One\nTwo\nThree"


def test_bytes_neither_utf8_nor_cp1252_are_replaced_not_refused() -> None:
    # 0x81 is undefined in Windows-1252 and invalid UTF-8
    assert html_to_text(b"<p>A\x81B \x93quoted\x94</p>") == "A\ufffdB \u201cquoted\u201d"


def test_html_to_text_of_an_empty_document_is_empty() -> None:
    assert html_to_text(b"") == ""
    assert html_to_text(b"<html><body></body></html>") == ""


# --- extract_mda (Item 7 of the 10-K) -----------------------------------------------------------


def test_the_longest_item_7_section_wins_over_the_table_of_contents() -> None:
    out = extract_mda(ten_k(BODY), min_chars=1000)
    assert out.status == "ok"
    assert out.text is not None
    assert out.text.startswith("Item 7. Management\u2019s Discussion and Analysis of Financial")
    assert BODY.strip() in out.text
    assert "Item 7A" not in out.text and "Rates moved" not in out.text


@pytest.mark.parametrize(
    "end",
    [
        "Item 7A. Quantitative and Qualitative",
        "ITEM 8. FINANCIAL STATEMENTS AND",
        "Item 8 — Financial Statements",
    ],
)
def test_the_section_ends_at_item_7a_or_item_8(end: str) -> None:
    out = extract_mda(ten_k(BODY, toc=False, end=end), min_chars=1000)
    assert out.text is not None and "Rates moved" not in out.text


@pytest.mark.parametrize(
    "heading",
    [
        "ITEM 7. MANAGEMENT'S DISCUSSION",
        "Item 7 - Management\u2019s Discussion",
        "Item 7: Management\u2019s Discussion",
        "Item 7.\nManagement\u2019s Discussion",
        "Item 7 Management\u2019s Discussion",
    ],
)
def test_heading_variants(heading: str) -> None:
    text = f"Item 1. Business\nWe make things.\n{heading}\n{BODY}\nItem 8. Financial Statements\n"
    assert extract_mda(text, min_chars=1000).status == "ok"


def test_cross_references_at_a_line_start_are_not_headings() -> None:
    """Older 10-K HTML breaks lines mid-sentence, so a cross-reference can start a line
    (Apple's 2015 10-K): only a heading followed by its item's title (or nothing) counts."""
    text = (
        "Item 1. Business\nItem 7 of this Form 10-K under the heading MD&A describes trends.\n"
        "Item 7. Management\u2019s Discussion and Analysis\n"
        f"{BODY}\nItem 8 of this Form 10-K. All information presented herein is audited.\n"
        f"{BODY}\nItem 8. Financial Statements and Supplementary Data\nNumbers.\n"
    )
    out = extract_mda(text, min_chars=1000)
    assert out.text is not None
    assert out.text.startswith("Item 7. Management")
    assert "Item 8 of this Form 10-K" in out.text and "Numbers." not in out.text


def test_a_long_line_naming_a_statement_does_not_end_the_exhibit_mda() -> None:
    long_line = "Consolidated Statements of Cash Flows are discussed here " + "at length " * 12
    text = (
        "MANAGEMENT'S DISCUSSION AND ANALYSIS\n"
        f"{BODY}\n{long_line}\n{BODY}\nCONSOLIDATED BALANCE SHEETS\nAssets.\n"
    )
    out = extract_exhibit_mda(text, min_chars=1000)
    assert out.text is not None and long_line in out.text and "Assets." not in out.text


def test_item_7a_is_not_item_7() -> None:
    text = f"Item 7A. Quantitative and Qualitative Disclosures\n{BODY}\nItem 8. Statements\n"
    assert extract_mda(text, min_chars=1000) == MdaExtract("not_found", None)


def test_item_7_inside_a_sentence_is_not_a_heading() -> None:
    text = f"As discussed in Item 7. Management's Discussion, {BODY}\nItem 8. Statements\n"
    assert extract_mda(text, min_chars=1000) == MdaExtract("not_found", None)


def test_a_section_pointing_to_exhibit_13_is_by_reference() -> None:
    pointer = (
        "The information required by Item 7 is shown in Exhibit 13 and is incorporated by "
        "reference into this Form 10-K."
    )
    assert extract_mda(ten_k(pointer), min_chars=1000) == MdaExtract("by_reference", None)


def test_a_short_section_without_a_reference_is_not_found() -> None:
    assert extract_mda(ten_k("Not applicable."), min_chars=1000) == MdaExtract("not_found", None)


def test_min_chars_is_inclusive() -> None:
    out = extract_mda(ten_k(BODY, toc=False), min_chars=1000)
    assert out.text is not None
    assert extract_mda(ten_k(BODY, toc=False), min_chars=len(out.text)).status == "ok"
    assert extract_mda(ten_k(BODY, toc=False), min_chars=len(out.text) + 1).status == "not_found"


def test_a_last_section_runs_to_the_end_of_the_document() -> None:
    text = f"Item 1. Business\nWe make things.\nItem 7. Management's Discussion\n{BODY}"
    out = extract_mda(text, min_chars=1000)
    assert out.text is not None and out.text.endswith(BODY.strip())


def test_no_item_7_is_not_found() -> None:
    assert extract_mda("Item 1. Business\n" + BODY, min_chars=1000) == MdaExtract("not_found", None)
    assert extract_mda("", min_chars=1000) == MdaExtract("not_found", None)


def test_the_longest_section_wins_whatever_its_letters() -> None:
    """A lower-case table-of-contents line sorts after the real heading: length decides."""
    text = (
        "Table of Contents\nitem 7. management's discussion 20\nItem 7A. Quantitative 30\n"
        f"Item 7. Management's Discussion and Analysis\n{BODY}\nItem 8. Financial Statements\n"
    )
    out = extract_mda(text, min_chars=1000)
    assert out.text is not None and out.text.startswith("Item 7. Management's Discussion")


def test_one_character_is_the_smallest_min_chars() -> None:
    assert extract_mda(ten_k(BODY), min_chars=1).status == "ok"
    assert extract_exhibit_mda(exhibit(), min_chars=1).status == "ok"
    assert extract_exhibit_mda("no heading here", min_chars=1) == MdaExtract("not_found", None)


def test_exhibit_min_chars_is_inclusive_and_headings_are_at_most_150_characters() -> None:
    out = extract_exhibit_mda(exhibit(toc=False), min_chars=1000)
    assert out.text is not None
    assert extract_exhibit_mda(exhibit(toc=False), min_chars=len(out.text)).status == "ok"
    heading = "MANAGEMENT'S DISCUSSION AND ANALYSIS"
    for width, status in ((150, "ok"), (151, "not_found")):
        line = heading + " " * (width - len(heading) - 1) + "X"
        text = f"{line}\n{BODY}\nCONSOLIDATED BALANCE SHEETS\n"
        assert extract_exhibit_mda(text, min_chars=1000).status == status, width


def test_min_chars_must_be_positive() -> None:
    with pytest.raises(ValueError, match="min_chars"):
        extract_mda(BODY, min_chars=0)
    with pytest.raises(ValueError, match="min_chars"):
        extract_exhibit_mda(BODY, min_chars=0)


# --- extract_exhibit_mda (Exhibit 13) -----------------------------------------------------------


def exhibit(
    *, toc: bool = True, end: str = "REPORT OF INDEPENDENT REGISTERED PUBLIC ACCOUNTING FIRM"
) -> str:
    contents = (
        "CONSOLIDATED STATEMENTS OF INCOME 2\n"
        "MANAGEMENT\u2019S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND RESULTS OF "
        "OPERATIONS 44\n"
        if toc
        else ""
    )
    return (
        f"{contents}CONSOLIDATED STATEMENTS OF INCOME\n(in millions)\nRevenues 100\n"
        "MANAGEMENT\u2019S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND RESULTS OF "
        "OPERATIONS\n"
        f"{BODY}\n{end}\nTo the Board of Directors\n"
    )


def test_the_exhibit_mda_runs_from_its_heading_to_the_next_statement_heading() -> None:
    out = extract_exhibit_mda(exhibit(), min_chars=1000)
    assert out.status == "ok"
    assert out.text is not None
    assert out.text.startswith("MANAGEMENT\u2019S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION")
    assert BODY.strip() in out.text and "Board of Directors" not in out.text


@pytest.mark.parametrize(
    "end",
    [
        "Consolidated Statements of Cash Flows",
        "Consolidated Balance Sheets",
        "Quantitative and Qualitative Disclosures About Market Risk",
        "Management's Report on Internal Control Over Financial Reporting",
    ],
)
def test_exhibit_end_headings(end: str) -> None:
    out = extract_exhibit_mda(exhibit(toc=False, end=end), min_chars=1000)
    assert out.text is not None and "Board of Directors" not in out.text


def test_an_exhibit_without_an_mda_heading_or_too_short_is_not_found() -> None:
    assert extract_exhibit_mda(BODY, min_chars=1000) == MdaExtract("not_found", None)
    short = "MANAGEMENT'S DISCUSSION AND ANALYSIS\nShort.\nCONSOLIDATED BALANCE SHEETS\n"
    assert extract_exhibit_mda(short, min_chars=1000) == MdaExtract("not_found", None)


def test_a_sentence_mentioning_the_mda_is_not_a_heading() -> None:
    line = (
        "The following discussion should be read with management's discussion and analysis of "
        "financial condition and results of operations, which describes the business in more "
        "detail than this short line does."
    )
    assert extract_exhibit_mda(f"{line}\n{BODY}", min_chars=1000) == MdaExtract("not_found", None)


def test_an_exhibit_heading_may_say_management_discussion() -> None:
    """IBM's annual report titles its MD&A "Management Discussion" (no "'s")."""
    text = (
        f"Management Discussion\n{BODY}\nReport of Independent Registered Public Accounting Firm\n"
    )
    assert extract_exhibit_mda(text, min_chars=1000).status == "ok"
