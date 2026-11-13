"""Phase 2 checkpoint, Tier 3 item 3: the wide comparison of the per-company fundamentals
pipeline with the pandas implementation it replaced (the unit suite compares four random
companies and AAPL; this compares thirty more, with competing quarters and fiscal years, and the
recorded MSFT and JPM facts). Offline: no lake needed."""

import sys
from pathlib import Path

import pytest

_HELPERS = Path(__file__).resolve().parents[1] / "unit" / "data" / "fundamentals"
if str(_HELPERS) not in sys.path:
    sys.path.insert(0, str(_HELPERS))

from fundamentals_helpers import JPM, MSFT, fixture_facts  # noqa: E402
from test_fundamentals_reference import (  # noqa: E402
    UNIT_SEEDS,
    check_company,
    paths_exercised,
    random_company,
)

pytestmark = pytest.mark.integration

SWEEP_SEEDS = tuple(range(max(UNIT_SEEDS) + 1, max(UNIT_SEEDS) + 31))


@pytest.mark.parametrize("seed", SWEEP_SEEDS)
def test_random_company_matches_the_pandas_reference(seed: int) -> None:
    assert check_company(random_company(seed), f"seed {seed}") > 20


@pytest.mark.parametrize(("cik", "symbol"), [(MSFT, "MSFT"), (JPM, "JPM")])
def test_recorded_company_matches_the_pandas_reference(cik: int, symbol: str) -> None:
    assert check_company(fixture_facts(cik, symbol), symbol) > 30


def test_the_sweep_exercises_competing_periods() -> None:
    """Some companies of the sweep report two quarters, and two fiscal years, from one start
    date (the cases where the order of candidates decides the result)."""
    quarters = years = 0
    for seed in SWEEP_SEEDS:
        flows = random_company(seed)
        flows = flows[flows["duration_days"] > 0]
        per_start = flows.groupby(["tag", "period_start"])["duration_days"]
        quarters += int((per_start.apply(lambda d: ((d >= 80) & (d <= 100)).sum()) > 1).sum())
        years += int((per_start.apply(lambda d: ((d >= 350) & (d <= 380)).sum()) > 1).sum())
    assert quarters > 5 and years > 5
    sources, ttm_sources, _ = paths_exercised(SWEEP_SEEDS[:10])
    assert sources == {"reported", "derived"} and ttm_sources == {"annual", "quarters"}
