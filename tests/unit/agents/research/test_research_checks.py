"""Stage 30 Task 3: the report checks every research analyst shares
(`atlas.agents.research.checks`), moved out of the fundamental analyst unchanged."""

import pytest

from atlas.agents.research import checks, fundamental


def test_sentences_split_after_stops_and_lines_and_keep_trailing_tags() -> None:
    text = "Revenue grew 5%. [E:a:1]\nMargins fell! Why? Because.\n\n  [E:b:2]  "
    assert checks.sentences(text) == [
        "Revenue grew 5%. [E:a:1]",
        "Margins fell!",
        "Why?",
        "Because. [E:b:2]",
    ]


@pytest.mark.parametrize(
    ("text", "uncited"),
    [
        ("Close 182.5 [E:pxs:A:2024-02-29:ohlc].", []),
        ("Close 182.5.", ["Close 182.5."]),
        ("As of 2024-02-29 the trend is up.", []),
        ("Only a tag [E:x:2024-01-01:1] here.", []),
        ("Up 5%. Down. RSI 70.", ["Up 5%.", "RSI 70."]),
    ],
)
def test_uncited_numbers(text: str, uncited: list[str]) -> None:
    assert checks.uncited_numbers(text) == uncited


def test_missing_sections_takes_the_sections_in_order() -> None:
    sections = ("Trend", "Levels")
    assert checks.missing_sections("Trend: up.\nLevels: 180.", sections) == []
    assert checks.missing_sections("Levels: 180.\nTrend: up.", sections) == ["Levels"]
    assert checks.missing_sections("**Trend**: up. Levels: 180.", sections) == ["Levels"]
    assert checks.missing_sections("", sections) == ["Trend", "Levels"]
    assert checks.missing_sections("## trend : up\n- LEVELS: 1", sections) == []


def test_quote_lists_five_sentences_cut_at_120_characters() -> None:
    items = [f"s{i} " + "x" * 200 for i in range(7)]
    text = checks.quote(items)
    assert text.count('"') == 10 and text.endswith(" and 2 more")
    assert f'"{items[0][:120]}"' in text
    assert checks.quote(["a", "b"]) == '"a"; "b"'
    assert checks.MAX_LISTED == 5


def test_the_fundamental_analyst_keeps_its_names() -> None:
    summary = "\n".join(f"{s}: ok." for s in fundamental.SECTIONS)
    assert fundamental.missing_sections(summary) == []
    assert fundamental.missing_sections("Valuation: ok.") == [
        s for s in fundamental.SECTIONS if s != "Valuation"
    ]
    assert fundamental.sentences is checks.sentences
    assert fundamental.uncited_numbers is checks.uncited_numbers
    assert fundamental.MAX_LISTED == checks.MAX_LISTED


# --- ungrounded_numbers (audit M4): each number must be in the evidence the sentence cites -------

EVIDENCE = {
    "rev": "AAPL revenue growth +30.0% year on year; revenue USD 385.7B on 2023-12-30.",
    "mar": "AAPL TTM margins: gross 45.0%, operating 30.8%, net margin 12%.",
    "px": "close 182.5 USD; SMA200 181.18; ret_5d +2.06% (own 74, universe 84); 1,234 shares.",
}


@pytest.mark.parametrize(
    ("text", "ungrounded"),
    [
        # one tag no longer covers every number of the sentence
        (
            "Revenue grew 30% while the margin fell to 12%. [E:rev]",
            ["Revenue grew 30% while the margin fell to 12%. [E:rev]"],
        ),
        # several tags in one sentence cover the numbers each of them states
        ("Revenue grew 30% [E:rev] while the margin fell to 12% [E:mar].", []),
        ("Revenue grew 30% while the margin fell to 12%. [E:rev][E:mar]", []),
        # a number the cited item does not hold, even if another item holds it
        (
            "Close 182.5 and revenue USD 385.7B [E:px].",
            ["Close 182.5 and revenue USD 385.7B [E:px]."],
        ),
        # equal values with other formatting, and sign-insensitive matching
        ("Growth was 30% [E:rev].", []),
        ("Shares 1234 [E:px]; ret_5d -2.06% [E:px].", []),
        ("Shares 1,234 [E:px].", []),
        # the model rounded the evidence further, never to another value
        ("SMA200 181.2 [E:px].", []),
        ("SMA200 181.3 [E:px].", ["SMA200 181.3 [E:px]."]),
        # identifiers and the parts of dates in the evidence count as numbers it holds
        ("The 2023 filing, ret_5d at the 74th percentile [E:px][E:rev].", []),
        # not this check's business: no digits, no tag (uncited_numbers), unknown tag only
        ("Revenue grew strongly [E:rev].", []),
        ("Revenue grew 30%.", []),
        ("Revenue grew 30% [E:nope].", []),
        # ISO dates in the sentence are ignored, like in uncited_numbers
        ("As of 2024-02-29 revenue grew 30% [E:rev].", []),
    ],
)
def test_ungrounded_numbers(text: str, ungrounded: list[str]) -> None:
    assert checks.ungrounded_numbers(text, EVIDENCE) == ungrounded


def test_ungrounded_numbers_checks_each_known_tag_of_the_sentence_only() -> None:
    text = "Growth 30% [E:rev] and margin 12.5% [E:mar][E:nope]."
    assert checks.ungrounded_numbers(text, EVIDENCE) == [text]
    assert checks.ungrounded_numbers("", EVIDENCE) == []
    assert checks.ungrounded_numbers("Growth 30% [E:rev].", {}) == []


def test_ungrounded_numbers_is_public_and_the_analysts_use_it() -> None:
    assert "ungrounded_numbers" in checks.__all__
    assert fundamental.ungrounded_numbers is checks.ungrounded_numbers
