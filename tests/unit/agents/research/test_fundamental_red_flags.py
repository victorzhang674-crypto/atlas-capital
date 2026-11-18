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


def test_going_concern_rule() -> None:
    assert going_concern_rule(None, CFG).status == "NOT EVALUATED"
    assert "no MD&A text" in going_concern_rule(None, CFG).detail
    hit = going_concern_rule("substantial doubt exists", CFG)
    assert (hit.check, hit.status) == ("going_concern", "FLAG")
    assert '"substantial doubt"' in hit.detail
    assert going_concern_rule("Revenue grew.", CFG).status == "CLEAR"


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
