"""Tests for prefixed ULID ids."""

import re
import threading
from datetime import date, datetime

import pytest
from pydantic import ValidationError
from ulid import ULID

from atlas.core.clock import SimClock
from atlas.core.types.base import AtlasModel
from atlas.core.types.ids import (
    IdPrefix,
    RandomIds,
    SeededIds,
    ThesisId,
    get_ids,
    new_id,
    use_ids,
)

ULID_RE = "[0-7][0-9A-HJKMNP-TV-Z]{25}"


class _HasThesisId(AtlasModel):
    id: ThesisId


def test_new_id_format() -> None:
    first, second = new_id("th"), new_id("th")
    assert re.fullmatch(f"th_{ULID_RE}", first)
    assert first != second


def test_new_id_accepts_enum_prefix() -> None:
    assert new_id(IdPrefix.ORDER).startswith("ord_")


@pytest.mark.parametrize("prefix", ["", "t", "TH", "th_", "toolong", "t1", "th "])
def test_new_id_rejects_bad_prefix(prefix: str) -> None:
    with pytest.raises(ValueError, match="prefix"):
        new_id(prefix)


def test_id_prefix_values() -> None:
    assert {p.name: p.value for p in IdPrefix} == {
        "THESIS": "th",
        "PROPOSAL": "prop",
        "ORDER": "ord",
        "FILL": "fill",
        "PREDICTION": "pred",
        "LESSON": "les",
        "LEDGER": "led",
    }


def test_prefixed_id_rejects_wrong_prefix() -> None:
    good = new_id(IdPrefix.THESIS)
    assert _HasThesisId(id=good).id == good
    for bad in (new_id(IdPrefix.ORDER), "th_123", good.lower(), good + "X", f" {good}"):
        with pytest.raises(ValidationError):
            _HasThesisId(id=bad)


def test_prefixed_id_rejects_impossible_ulid() -> None:  # Phase 0 checkpoint, Tier 2
    # The first ULID character holds the top 3 bits of the 48-bit timestamp: only 0-7 exist.
    highest = "th_7" + "Z" * 25
    assert _HasThesisId(id=highest).id == highest
    for first in "89ABZ":
        with pytest.raises(ValidationError):
            _HasThesisId(id=f"th_{first}" + "0" * 25)


# --- Phase 0 checkpoint, Tier 3: reproducible ids --------------------------------------------


def _seeded_run(seed: int) -> tuple[list[str], list[datetime]]:
    """Mint ids the way a backtest would: three at one simulated instant, one after advancing."""
    clock = SimClock(start=date(2024, 3, 1))
    with use_ids(SeededIds(seed, clock)):
        ids = [new_id(IdPrefix.THESIS) for _ in range(3)]
        first = clock.now()
        clock.advance()
        ids.append(new_id(IdPrefix.ORDER))
    return ids, [first, clock.now()]


def test_seeded_ids_repeat_for_the_same_seed() -> None:
    ids, _ = _seeded_run(7)
    assert _seeded_run(7)[0] == ids
    assert _seeded_run(8)[0] != ids
    assert all(_HasThesisId(id=i) for i in ids[:3])


def test_seeded_ids_carry_the_clock_time_and_sort_in_creation_order() -> None:
    ids, (first, second) = _seeded_run(7)
    stamps = [ULID.from_str(i.split("_", 1)[1]).datetime for i in ids]
    assert stamps == [first, first, first, second]
    ulids = [i.split("_", 1)[1] for i in ids]
    assert ulids == sorted(ulids)
    assert len(set(ulids)) == 4


def test_seeded_ids_reject_a_naive_clock() -> None:
    class _Naive:
        def now(self) -> datetime:
            return datetime(2024, 3, 1, 13, 0)

    with use_ids(SeededIds(1, _Naive())), pytest.raises(ValueError, match="naive"):
        new_id(IdPrefix.THESIS)


def test_default_ids_are_random_and_use_ids_restores() -> None:
    assert isinstance(get_ids(), RandomIds)
    seeded = SeededIds(7, SimClock(start=date(2024, 3, 1)))
    with use_ids(seeded):
        assert get_ids() is seeded
        inner = SeededIds(8, SimClock(start=date(2024, 3, 1)))
        with use_ids(inner):
            assert get_ids() is inner
        assert get_ids() is seeded
    assert isinstance(get_ids(), RandomIds)
    assert new_id(IdPrefix.THESIS) != new_id(IdPrefix.THESIS)


def test_new_thread_mints_random_ids() -> None:
    seen: list[object] = []
    with use_ids(SeededIds(7, SimClock(start=date(2024, 3, 1)))):
        thread = threading.Thread(target=lambda: seen.append(get_ids()))
        thread.start()
        thread.join()
    assert isinstance(seen[0], RandomIds)
