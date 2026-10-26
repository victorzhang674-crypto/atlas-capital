"""Tests for the shared model base and field types of `atlas.core.types`."""

import pickle
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from atlas.core.types.base import AtlasModel, FrozenDict, NonEmptyStr, UnitInterval, UtcDatetime


class _Sample(AtlasModel):
    x: float
    p: UnitInterval = 0.5
    name: NonEmptyStr = "n"
    ts: UtcDatetime | None = None


def test_atlas_model_frozen_forbid_no_nan() -> None:
    m = _Sample(x=1.0)
    with pytest.raises(ValidationError):
        _Sample.model_validate({"x": 1.0, "extra": 1})
    with pytest.raises(ValidationError):
        m.x = 2.0  # type: ignore[misc]
    with pytest.raises(ValidationError):
        _Sample(x=float("nan"))
    with pytest.raises(ValidationError):
        _Sample(x=float("inf"))


def test_frozen_dict_readonly_and_equal() -> None:
    d = FrozenDict({"a": 1.0, "b": 2.0})
    with pytest.raises(TypeError):
        d["a"] = 3.0  # type: ignore[index]
    assert d == {"a": 1.0, "b": 2.0}
    assert dict(d) == {"a": 1.0, "b": 2.0}
    assert len(d) == 2
    assert pickle.loads(pickle.dumps(d)) == d


@pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
def test_unit_interval_accepts_bounds(value: float) -> None:
    assert _Sample(x=0, p=value).p == value


@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan")])
def test_unit_interval_bounds(value: float) -> None:
    with pytest.raises(ValidationError):
        _Sample(x=0, p=value)


def test_non_empty_str() -> None:
    assert _Sample(x=0, name="  ok ").name == "ok"
    with pytest.raises(ValidationError):
        _Sample(x=0, name="   ")
    with pytest.raises(ValidationError):
        _Sample(x=0, name="")


def test_utc_datetime_rejects_naive_and_converts() -> None:
    with pytest.raises(ValidationError):
        _Sample(x=0, ts=datetime(2024, 1, 2, 9, 30))
    local = datetime(2024, 1, 2, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    ts = _Sample(x=0, ts=local).ts
    assert ts == datetime(2024, 1, 2, 14, 30, tzinfo=UTC)
    assert ts is not None
    assert ts.utcoffset() is not None
    assert ts.tzinfo == UTC
