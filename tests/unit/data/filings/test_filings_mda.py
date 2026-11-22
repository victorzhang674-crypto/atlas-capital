"""Stage 29 follow-up 2 (F2): 10-K HTML to text, and the MD&A section of a 10-K (Item 7) or
of the annual-report exhibit (Exhibit 13) that Item 7 points to."""

import pytest

from atlas.data.filings.mda import (
    MdaExtract,
    extract_exhibit_mda,
    extract_mda,
    html_to_text,
    points_elsewhere,
)

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


# --- follow-up 3: 10-Q MD&As, "Financial Review", an MD&A later in the 10-K ----------------------


def ten_q(item2: str) -> str:
    return (
        "PART I. FINANCIAL INFORMATION\nItem 1. Financial Statements\nNumbers.\n"
        "Item 2. Management's Discussion and Analysis of Financial Condition\n"
        f"{item2}\nItem 3. Quantitative and Qualitative Disclosures About Market Risk\nRates.\n"
        "Item 4. Controls and Procedures\nEffective.\nPART II. OTHER INFORMATION\n"
        "Item 2. Unregistered Sales of Equity Securities and Use of Proceeds\nNone.\n"
    )


def test_a_10q_mda_is_part_i_item_2() -> None:
    out = extract_mda(ten_q(BODY), min_chars=1000, form="10-Q")
    assert out.status == "ok"
    assert out.text is not None
    assert out.text.startswith("Item 2. Management's Discussion")
    assert BODY.strip() in out.text and "Rates." not in out.text
    # a 10-Q has no Item 7, and a 10-K's Item 2 (Properties) is not its MD&A
    assert extract_mda(ten_q(BODY), min_chars=1000, form="10-K").status == "not_found"
    assert extract_mda(ten_k(BODY), min_chars=1000, form="10-Q").status == "not_found"


@pytest.mark.parametrize(
    "end",
    [
        "Item 3. Quantitative and Qualitative Disclosures",
        "ITEM 4. CONTROLS AND PROCEDURES",
        "PART II — OTHER INFORMATION",
    ],
)
def test_a_10q_mda_ends_at_item_3_item_4_or_part_ii(end: str) -> None:
    text = f"Item 2. Management's Discussion and Analysis\n{BODY}\n{end}\nAfter.\n"
    out = extract_mda(text, min_chars=1000, form="10-Q")
    assert out.text is not None and "After." not in out.text


@pytest.mark.parametrize(
    ("form", "kind"), [("10-K405", "10-K"), ("10-KT", "10-K"), ("10-Q/A", "10-Q")]
)
def test_form_variants_read_their_base_form(form: str, kind: str) -> None:
    text = ten_q(BODY) if kind == "10-Q" else ten_k(BODY)
    assert extract_mda(text, min_chars=1000, form=form).status == "ok"


def test_financial_review_is_an_exhibit_mda_heading() -> None:
    """Wells Fargo's annual report (Exhibit 13) titles its MD&A "Financial Review"."""
    text = (
        "Financial Review\nOverview 114\nFinancial Review\n"
        f"{BODY}\nConsolidated Statement of Income\nRevenue 1.\n"
    )
    out = extract_exhibit_mda(text, min_chars=1000)
    assert out.text is not None and out.text.startswith("Financial Review")
    # a sentence or a longer title naming it is not the heading
    assert extract_exhibit_mda(
        f"Our Financial Review of 2025\n{BODY}\n", min_chars=1000
    ) == MdaExtract("not_found", None)


def test_skip_items_takes_the_mda_heading_after_an_item_7_pointer() -> None:
    """AEP, Mosaic, Time Warner: Item 7 points to an annual-report section later in the same
    document, which has its own MD&A heading."""
    text = (
        "Item 7. Management's Discussion and Analysis of Financial Condition\n"
        "The information is in the Annual Report section, incorporated by reference.\n"
        "Item 8. Financial Statements\nSee the annual report.\nItem 15. Exhibits\nList.\n"
        f"Management's Discussion and Analysis of Results of Operations\n{BODY}\n"
        "Report of Independent Registered Public Accounting Firm\nOpinion.\n"
    )
    out = extract_exhibit_mda(text, min_chars=1000, skip_items=True)
    assert out.text is not None and out.text.startswith(
        "Management's Discussion and Analysis of Results"
    )
    assert "Item 8" not in out.text
    unskipped = extract_exhibit_mda(text, min_chars=1000)
    assert unskipped.text is not None and unskipped.text.startswith("Item 7.")


def test_a_bullet_of_the_mdas_own_contents_does_not_end_it() -> None:
    """Clorox's MD&A opens with a bulleted list of its parts; one bullet reads like the
    market-risk heading that would end it."""
    text = (
        "MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION\nThe MD&A has these parts:\n"
        "•\nContingencies\n•\nQuantitative and Qualitative Disclosure About Market Risk\n"
        f"{BODY}\nQuantitative and Qualitative Disclosure About Market Risk\nRates.\n"
    )
    out = extract_exhibit_mda(text, min_chars=1000)
    assert out.text is not None and BODY.strip() in out.text and "Rates." not in out.text


@pytest.mark.parametrize(
    "line",
    [
        "See “Management\u2019s Discussion and Analysis of Results of Operations” below.",
        "“Management\u2019s Discussion and Analysis of Results of Operations — Overview.”",
        "7 Management\u2019s Discussion and Analysis of Financial Condition 39",
    ],
)
def test_cross_references_and_contents_lines_are_not_mda_headings(line: str) -> None:
    """Time Warner's cross-references and AEP's table of contents start lines that name the
    MD&A; the heading itself comes later."""
    text = (
        f"{line}\n{BODY}\nItem 9A. Controls\n"
        f"Management\u2019s Discussion and Analysis\n{BODY}\nConsolidated Balance Sheets\nAssets.\n"
    )
    out = extract_exhibit_mda(text, min_chars=1000)
    assert out.text is not None and out.text.startswith(
        "Management\u2019s Discussion and Analysis\n"
    )


def test_a_company_name_may_precede_an_upper_case_mda_heading() -> None:
    """AEP's combined 10-K heads each registrant's MD&A with the company's name."""
    heading = (
        "AMERICAN ELECTRIC POWER COMPANY, INC. AND SUBSIDIARY COMPANIES MANAGEMENT'S DISCUSSION "
        "AND ANALYSIS OF FINANCIAL CONDITION AND"
    )
    text = (
        f"{heading}\n{BODY}\nQuantitative and Qualitative Disclosures About Market Risk\nRates.\n"
    )
    out = extract_exhibit_mda(text, min_chars=1000)
    assert out.text is not None and out.text.startswith("AMERICAN ELECTRIC POWER")
    # in mixed case a long prefix is a sentence, not a heading
    sentence = "For more on our segments and our results, read the management's discussion below"
    assert extract_exhibit_mda(f"{sentence}\n{BODY}\n", min_chars=1000).status == "not_found"


def test_financial_review_may_carry_a_footnote_mark() -> None:
    text = f"Financial Review1\n{BODY}\nConsolidated Statement of Income\nRevenue 1.\n"
    out = extract_exhibit_mda(text, min_chars=1000)
    assert out.text is not None and out.text.startswith("Financial Review1")


def test_a_page_number_glued_to_the_heading_does_not_hide_it() -> None:
    """IBM's text reads "18Management Discussion": the previous page's number runs into the
    heading. A contents entry has the number apart ("7 Management's Discussion ... 39")."""
    text = (
        f"18Management Discussion\n{BODY}\n19\nManagement Discussion\nMore.\n"
        "Consolidated Balance Sheets\n"
    )
    out = extract_exhibit_mda(text, min_chars=1000)
    assert out.text is not None and out.text.startswith("18Management Discussion")


def test_an_inline_bullet_entry_does_not_end_the_mda_either() -> None:
    text = (
        "MANAGEMENT'S DISCUSSION AND ANALYSIS\n• Contingencies\n"
        "Quantitative and Qualitative Disclosures About Market Risk\n"
        f"{BODY}\nQuantitative and Qualitative Disclosures About Market Risk\nRates.\n"
    )
    out = extract_exhibit_mda(text, min_chars=1000)
    assert out.text is not None and BODY.strip() in out.text and "Rates." not in out.text


# --- follow-up 3 (H4 review): Part II Item 2, Entergy's MD&A heading ------------------------------


def test_part_ii_item_2_listed_in_the_contents_is_not_a_10q_mda_heading() -> None:
    """A bare "Item 2." line is the MD&A heading only when "Management..." follows on the same
    or the next line: the contents entry of Part II Item 2 ("Item 2." / "Unregistered Sales
    ...") would otherwise run through the financial statements to Part I Item 3."""
    text = "\n".join(
        [
            "PART I",
            "Item 2.",
            "Management\u2019s Discussion and Analysis",
            "Item 3.",
            "Quantitative and Qualitative Disclosures",
            "PART II",
            "Item 2.",
            "Unregistered Sales of Equity Securities and Use of Proceeds",
            "PART I",
            "Item 1. Financial Statements",
            "Balance sheet. " * 200,
            "Item 2.",
            "Management\u2019s Discussion and Analysis of Financial Condition",
            BODY,
            "Item 3.",
            "Quantitative and Qualitative Disclosures About Market Risk",
            "PART II",
            "Item 2.",
            "Unregistered Sales of Equity Securities and Use of Proceeds",
            "Purchases. " * 300,
        ]
    )
    out = extract_mda(text, min_chars=1000, form="10-Q")
    assert out.text is not None
    assert out.text.startswith("Item 2.\nManagement\u2019s Discussion and Analysis of Financial")
    assert "Balance sheet." not in out.text and "Purchases." not in out.text
    assert BODY.strip() in out.text
    # without any MD&A title after it, a bare Item 2 is no heading at all
    bare = f"Item 2.\nUnregistered Sales of Equity Securities\n{BODY}\n"
    assert extract_mda(bare, min_chars=1000, form="10-Q").status == "not_found"


def test_management_s_financial_discussion_is_an_exhibit_mda_heading() -> None:
    """Entergy's combined 10-K heads each registrant's MD&A "MANAGEMENT'S FINANCIAL DISCUSSION
    AND ANALYSIS"."""
    text = (
        "ENTERGY CORPORATION AND SUBSIDIARIES\nMANAGEMENT'S FINANCIAL DISCUSSION AND ANALYSIS\n"
        f"{BODY}\nCONSOLIDATED BALANCE SHEETS\nAssets.\n"
    )
    out = extract_exhibit_mda(text, min_chars=1000)
    assert out.text is not None and out.text.startswith("MANAGEMENT'S FINANCIAL DISCUSSION")
    assert "Assets." not in out.text
    mixed = f"Management\u2019s Financial Discussion and Analysis\n{BODY}\n"
    assert extract_exhibit_mda(mixed, min_chars=1000).status == "ok"


@pytest.mark.parametrize(
    "end",
    [
        "CONDENSED CONSOLIDATED STATEMENTS OF INCOME (UNAUDITED)",
        "Condensed Consolidated Balance Sheets",
    ],
)
def test_condensed_statements_end_an_exhibit_mda(end: str) -> None:
    """A 10-Q's statements are "condensed" (Southern Company's combined 10-Q)."""
    text = f"MANAGEMENT'S DISCUSSION AND ANALYSIS\n{BODY}\n{end}\nRevenue.\n"
    out = extract_exhibit_mda(text, min_chars=1000)
    assert out.text is not None and "Revenue." not in out.text


def test_points_elsewhere() -> None:
    assert points_elsewhere("The MD&A is incorporated herein by reference to the report.")
    assert points_elsewhere("See Exhibit 13.")
    assert not points_elsewhere(BODY)


@pytest.mark.parametrize(
    ("form", "item"),
    [("10-Q", "ITEM 2.\nMANAGMENT'S DISCUSSION"), ("10-K", "ITEM 7. MANAGMENT\u2019S DISCUSSION")],
)
def test_a_heading_that_misspells_management_is_still_read(form: str, item: str) -> None:
    """Roper's 10-Q of 2017-11 heads its MD&A "MANAGMENT'S DISCUSSION AND ANALYSIS"; with the
    Part II rule a bare "Item 2." needs the word after it, so the misspelling must count."""
    end = "Item 3. Quantitative" if form == "10-Q" else "Item 8. Financial Statements"
    out = extract_mda(f"{item} AND ANALYSIS\n{BODY}\n{end}\nAfter.\n", min_chars=1000, form=form)
    assert out.status == "ok"
    assert out.text is not None and out.text.startswith(item) and "After." not in out.text
    # the Part II entry stays no heading
    bare = f"Item 2.\nUnregistered Sales of Equity Securities\n{BODY}\n"
    assert extract_mda(bare, min_chars=1000, form="10-Q").status == "not_found"
