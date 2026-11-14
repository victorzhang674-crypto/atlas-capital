"""Stage 26 Task 3: `AgentContext`."""

from datetime import date

import pytest

from atlas.agents.context import AgentContext
from atlas.core.pit import AsOf
from atlas.core.run_context import run_context
from atlas.core.storage import Lake
from atlas.data.view import DataView


def test_as_of_comes_from_the_view(tmp_lake: Lake) -> None:
    view = DataView(tmp_lake, date(2024, 3, 1))
    ctx = AgentContext(view)
    assert ctx.view is view
    assert ctx.as_of == view.as_of == AsOf.for_session(date(2024, 3, 1))
    assert ctx.day == date(2024, 3, 1)
    assert ctx.memory is None and dict(ctx.inputs) == {}


def test_run_id_defaults_to_the_current_run(tmp_lake: Lake) -> None:
    view = DataView(tmp_lake, date(2024, 3, 1))
    assert AgentContext(view).run_id is None
    with run_context() as run:
        assert AgentContext(view).run_id == run.run_id
        assert AgentContext(view, run_id="X" * 26).run_id == "X" * 26


def test_inputs_are_frozen_copies(tmp_lake: Lake) -> None:
    raw = {"symbol": "AAA"}
    ctx = AgentContext(DataView(tmp_lake, date(2024, 3, 1)), inputs=raw, memory=object())
    raw["symbol"] = "BBB"
    assert ctx.inputs["symbol"] == "AAA"
    with pytest.raises(TypeError):
        ctx.inputs["symbol"] = "CCC"  # type: ignore[index]
    with pytest.raises(AttributeError):
        ctx.run_id = "x"  # type: ignore[misc]


def test_empty_run_id_is_refused(tmp_lake: Lake) -> None:
    with pytest.raises(ValueError, match="run_id"):
        AgentContext(DataView(tmp_lake, date(2024, 3, 1)), run_id="")
