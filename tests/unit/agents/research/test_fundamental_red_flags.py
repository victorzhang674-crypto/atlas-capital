"""Stage 29 Task 2: the red-flag rules on synthetic numbers (flag, clear, not evaluated, at
and around each threshold)."""

import math

import pytest

from atlas.agents.tools.fundamental_tools import (
    accruals_rule,
    dilution_rule,
    fcf_debt_rule,
    going_concern_hits,
    going_concern_rule,
    going_concern_sentences,
    receivables_rule,
)
from atlas.core.config import RedFlagsConfig

CFG = RedFlagsConfig()
NAN = math.nan


def test_the_default_thresholds() -> None:
    assert CFG.accruals_max == 0.10
    assert CFG.accruals_jump == 0.05
    assert CFG.receivables_excess_growth == 0.15
    assert CFG.debt_growth_min == 0.10
    assert CFG.dilution_max == 0.05
    assert CFG.going_concern_terms == ("going concern", "substantial doubt")


@pytest.mark.parametrize(
    ("ratio", "before", "status"),
    [
        (0.02, 0.01, "CLEAR"),
        (0.10, 0.08, "CLEAR"),  # at the level threshold: not above it
        (0.1001, 0.09, "FLAG"),  # above the level
        (0.07, 0.01, "FLAG"),  # a jump of 6 points to a positive level
        (0.06, 0.01, "CLEAR"),  # a jump of exactly 5 points is not more than 5
        (-0.01, -0.09, "CLEAR"),  # a jump, but to a negative level (cash ahead of earnings)
        (0.08, NAN, "CLEAR"),  # no year-ago ratio: only the level counts
        (0.12, NAN, "FLAG"),
        (NAN, 0.01, "NOT EVALUATED"),
    ],
)
def test_accruals(ratio: float, before: float, status: str) -> None:
    flag = accruals_rule(ratio, before, CFG)
    assert (flag.check, flag.status) == ("accruals", status)
    if status != "NOT EVALUATED":
        assert f"{ratio * 100:.1f}%" in flag.detail


@pytest.mark.parametrize(
    ("ar", "revenue", "status"),
    [
        (0.40, 0.10, "FLAG"),
        (0.25, 0.10, "CLEAR"),  # excess of exactly 15 points
        (0.2501, 0.10, "FLAG"),
        (-0.05, -0.30, "FLAG"),  # receivables fall less than revenue: the excess counts
        (0.05, 0.10, "CLEAR"),
        (NAN, 0.10, "NOT EVALUATED"),
        (0.40, NAN, "NOT EVALUATED"),
    ],
)
def test_receivables(ar: float, revenue: float, status: str) -> None:
    flag = receivables_rule(ar, revenue, CFG)
    assert (flag.check, flag.status) == ("receivables", status)


def test_receivables_detail_states_both_growth_rates_and_the_excess() -> None:
    flag = receivables_rule(0.40, 0.10, CFG)
    assert "+40.0%" in flag.detail and "+10.0%" in flag.detail
    assert "excess +30.0 points (flag above 15.0 points)" in flag.detail


@pytest.mark.parametrize(
    ("fcf", "debt", "status"),
    [
        (-1e8, 0.20, "FLAG"),
        (-1e8, 0.10, "CLEAR"),  # exactly the threshold
        (-1e8, 0.1001, "FLAG"),
        (-1e8, -0.05, "CLEAR"),  # burning cash but paying debt down
        (-1e8, math.inf, "FLAG"),  # new debt from none
        (0.0, 0.50, "CLEAR"),  # free cash flow not negative
        (5e8, 0.50, "CLEAR"),
        (-1e8, NAN, "NOT EVALUATED"),
        (NAN, 0.50, "NOT EVALUATED"),
    ],
)
def test_negative_fcf_with_rising_debt(fcf: float, debt: float, status: str) -> None:
    flag = fcf_debt_rule(fcf, debt, CFG)
    assert (flag.check, flag.status) == ("fcf_debt", status)


def test_new_debt_is_named_as_such() -> None:
    assert "new debt" in fcf_debt_rule(-1e8, math.inf, CFG).detail
    assert "USD -100M" in fcf_debt_rule(-1e8, 0.2, CFG).detail


@pytest.mark.parametrize(
    ("growth", "status"),
    [(0.06, "FLAG"), (0.05, "CLEAR"), (0.0501, "FLAG"), (-0.03, "CLEAR"), (NAN, "NOT EVALUATED")],
)
def test_dilution(growth: float, status: str) -> None:
    flag = dilution_rule(growth, CFG)
    assert (flag.check, flag.status) == ("dilution", status)


def test_going_concern_terms_are_whole_words_ignoring_case_and_spacing() -> None:
    text = "There is SUBSTANTIAL\n doubt about our ability to continue as a Going Concern."
    assert going_concern_hits(text, CFG.going_concern_terms) == (
        "going concern",
        "substantial doubt",
    )
    assert going_concern_hits("an ongoing concerns list", CFG.going_concern_terms) == ()
    assert going_concern_hits("", CFG.going_concern_terms) == ()


DOUBT = "These conditions raise substantial doubt about our ability to continue as a going concern."


def test_going_concern_rule() -> None:
    """Stage 29 follow-up 2 (E5): a FLAG needs one sentence holding every term and no excluded
    phrase, and quotes it (real MD&As name the terms mostly to describe ASU 2014-15)."""
    assert going_concern_rule(None, CFG).status == "NOT EVALUATED"
    assert "no MD&A text" in going_concern_rule(None, CFG).detail
    hit = going_concern_rule(f"Revenue fell. {DOUBT} We plan to raise capital.", CFG)
    assert (hit.check, hit.status) == ("going_concern", "FLAG")
    assert hit.detail == f'MD&A: "{DOUBT}"'
    assert going_concern_rule("Revenue grew.", CFG).status == "CLEAR"
    # the terms in two sentences are not one statement
    assert (
        going_concern_rule("Substantial doubt? No. We are a going concern.", CFG).status == "CLEAR"
    )


@pytest.mark.parametrize(
    "sentence",
    [
        "ASU 2014-15 requires management to evaluate whether there is substantial doubt about an "
        "entity's ability to continue as a going concern.",
        "The new guidance requires an entity to evaluate whether there are conditions that raise "
        "substantial doubt about the entity\u2019s ability to continue as a going concern.",
        "GAAP guidance on evaluating whether there is substantial doubt about a company's ability "
        "to continue as a going concern.",
        "Management concluded that substantial doubt about our ability to continue as a going "
        "concern no longer exists.",
        "Based on our evaluation no conditions that could raise substantial doubt about our "
        "ability to continue as a going concern were known.",
        "There is no substantial doubt about our ability to continue as a going concern.",
        "If substantial doubt exists, the entity must disclose the conditions that raised the "
        "substantial doubt about its ability to continue as a going concern.",
        "This guidance will impact the disclosure of any substantial doubt about our ability to "
        "continue as a going concern, if such substantial doubt were to exist.",
        "We recorded $91 million for a tenant who noted substantial doubt as to their ability to "
        "continue as a going concern.",
        "Factors included the substantial doubt about the investee's ability to continue as a "
        "going concern, as disclosed by the investee.",
        "After emergence we concluded there was no longer substantial doubt regarding our ability "
        "to continue as a going concern.",
        # follow-up 3, 10-Q MD&As: a negation, a standard's wording, a borrower's doubt
        "As a result of the closing, substantial doubt regarding our ability to continue as a "
        "going concern does not exist.",
        "The Company must then provide certain disclosure if there is substantial doubt about its "
        "ability to continue as a going concern.",
        "Indicators include substantial doubt about the borrower\u2019s ability to continue as a "
        "going concern.",
    ],
)
def test_standards_others_and_negations_are_not_a_flag(sentence: str) -> None:
    assert (
        going_concern_sentences(sentence, CFG.going_concern_terms, CFG.going_concern_exclusions)
        == ()
    )
    assert going_concern_rule(sentence, CFG).status == "CLEAR"


def test_going_concern_sentences_are_unique_in_order_and_quoted_at_most_200_characters() -> None:
    long = (
        "Because "
        + "the restructuring may fail " * 10
        + "there is substantial doubt about our ability to continue as a going concern."
    )
    text = f"{DOUBT}\n{long} {DOUBT}"
    assert going_concern_sentences(text, CFG.going_concern_terms, CFG.going_concern_exclusions) == (
        DOUBT,
        long,
    )
    detail = going_concern_rule(f"{long} {DOUBT}", CFG).detail
    assert detail == f'MD&A: "{long[:199]}\u2026" and 1 more sentence'
    assert going_concern_rule(f"{DOUBT} {long} Again: {DOUBT.lower()}", CFG).detail.endswith(
        " and 2 more sentences"
    )


def test_a_sentence_cut_by_a_line_break_or_an_abbreviation_is_read_whole() -> None:
    """Stage 29 follow-up 3: a piece that starts with a lower-case letter goes on with the
    sentence before it (a line break inside a sentence, or a period after "Inc.")."""
    inc = (
        "In the fourth quarter, LiveWire Group, Inc. disclosed conditions that raise substantial "
        "doubt about its ability to continue as a going concern."
    )
    text = f"Revenue fell. {inc}\nWe hold 87%."
    assert going_concern_sentences(text, CFG.going_concern_terms, ()) == (inc,)
    assert going_concern_rule(text, CFG).detail.startswith('MD&A: "In the fourth quarter, Live')
    broken = (
        "Additionally, the entity must provide certain disclosures if conditions or events raise\n"
        "substantial doubt about its ability to continue as a going concern."
    )
    assert going_concern_sentences(broken, CFG.going_concern_terms, ()) == (
        " ".join(broken.split()),
    )
    assert going_concern_rule(broken, CFG).status == "CLEAR"
    # a piece starting with a capital, a digit or a bracket is a sentence of its own
    assert going_concern_sentences(f"Sales rose.\n(a) {DOUBT}", CFG.going_concern_terms, ()) == (
        f"(a) {DOUBT}",
    )
    assert going_concern_sentences(f"lower start. {DOUBT}", CFG.going_concern_terms, ()) == (DOUBT,)


def test_exclusions_ignore_case_and_curly_apostrophes() -> None:
    sentence = DOUBT.replace("our ability", "An Entity\u2019s ability")
    assert going_concern_sentences(sentence, CFG.going_concern_terms, ("an entity's",)) == ()
    assert going_concern_sentences(sentence, CFG.going_concern_terms, ()) == (sentence,)


def test_the_clear_detail_names_the_terms() -> None:
    assert going_concern_rule("Revenue grew.", CFG).detail == (
        'no MD&A sentence holds all of "going concern", "substantial doubt" (sentences with a '
        "phrase of going_concern_exclusions, such as a standard's name, are left out)"
    )


def test_thresholds_come_from_config() -> None:
    strict = RedFlagsConfig(dilution_max=0.01, accruals_max=0.01, going_concern_terms=("doubt",))
    assert dilution_rule(0.02, strict).status == "FLAG"
    assert accruals_rule(0.02, NAN, strict).status == "FLAG"
    assert going_concern_rule("some doubt", strict).status == "FLAG"


@pytest.mark.parametrize(
    "bad",
    [
        {"accruals_max": 0},
        {"accruals_jump": -0.1},
        {"receivables_excess_growth": 0},
        {"debt_growth_min": -0.1},
        {"dilution_max": 0},
        {"going_concern_terms": ("",)},
    ],
)
def test_bad_thresholds_are_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RedFlagsConfig.model_validate(bad)


def test_a_sentence_of_exactly_200_characters_is_quoted_whole() -> None:
    tail = " there is substantial doubt about our ability to continue as a going concern."
    sentence = "B" * (200 - len(tail)) + tail
    assert len(sentence) == 200
    assert going_concern_rule(sentence, CFG).detail == f'MD&A: "{sentence}"'


@pytest.mark.parametrize(
    "furniture", ["F-32\nTable of Contents", "45", "Index", "Page 12", "- 7 -"]
)
def test_page_furniture_inside_a_sentence_is_left_out(furniture: str) -> None:
    """Stage 29 follow-up 4: Windstream's 10-K of 2019-03-15 breaks its going-concern sentence
    across a page: "... master lease agreement" / "F-32" / "Table of Contents" / "with Uniti,
    ...". The page number and header between the two halves are not part of the sentence."""
    first = (
        "Our financial condition, the defaults under our debt agreements and master lease agreement"
    )
    second = (
        "with Uniti, and the risks and uncertainties surrounding the Chapter 11 Cases, raise "
        "substantial doubt about our ability to continue as a going concern."
    )
    text = f"Revenue fell.\n{first}\n{furniture}\n{second}\nWe filed a plan."
    assert going_concern_sentences(text, CFG.going_concern_terms, ()) == (f"{first} {second}",)
    # furniture followed by a capitalised sentence is kept as its own piece, and never joined
    kept = f"Revenue fell.\n{furniture}\n{DOUBT}"
    assert going_concern_sentences(kept, CFG.going_concern_terms, ()) == (DOUBT,)


def test_page_furniture_at_the_start_or_after_a_first_broken_sentence() -> None:
    """A page number that is the text's first piece stays (nothing comes before it to go on
    with); one right after the first sentence is dropped like any other."""
    second = "with Uniti, raise substantial doubt about our ability to continue as a going concern."
    assert going_concern_sentences(f"45\n{second}", CFG.going_concern_terms, ()) == (
        f"45 {second}",
    )
    first = "Our financial condition and the defaults under our master lease agreement"
    assert going_concern_sentences(f"{first}\n45\n{second}", CFG.going_concern_terms, ()) == (
        f"{first} {second}",
    )
