"""Tests for prefixed ULID ids."""

import re

import pytest
from pydantic import ValidationError

from atlas.core.types.base import AtlasModel
from atlas.core.types.ids import IdPrefix, ThesisId, new_id

ULID_RE = "[0-9A-HJKMNP-TV-Z]{26}"


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
