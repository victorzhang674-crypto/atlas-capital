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
