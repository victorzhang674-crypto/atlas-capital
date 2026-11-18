"""Integrity errors are told apart (Phase 3 checkpoint Tier 2 item 2).

The graph's policy is unchanged: a failing node is recorded and its symbol dropped. A
`LookAheadError` (data from after the cutoff) or a `CacheMiss` (a replay that is no longer
valid) is recorded the same way, but marked `integrity=True`, so a replay (Stage 36) or a
backtest (Stages 55 / 56) can find it with `integrity_errors(errors)` and fail the run instead
of silently researching fewer names.
"""

from typing import Any

import pytest
from graph_world import DAY, Spy, node_error, raiser

from atlas.core.errors import BudgetExceeded, CacheMiss, DataError, LLMError, LookAheadError
from atlas.orchestration import NodeError, integrity_errors
from atlas.orchestration.errors import INTEGRITY_ERRORS, failed_symbols, guarded
from atlas.orchestration.research_graph import build_research_graph

STATE: dict[str, Any] = {"symbol": "AAA", "as_of": DAY}
SYMBOLS = ["AAA", "BBB", "CCC"]


class _LateRow(LookAheadError):
    """A subclass is still an integrity error."""


def _fail(exc: Exception) -> NodeError:
    def work(state: dict[str, Any]) -> dict[str, Any]:
        raise exc

    [error] = guarded("news", work)(STATE)["errors"]
    assert isinstance(error, NodeError)
    return error


@pytest.mark.parametrize(
    "exc",
    [LookAheadError("future row"), CacheMiss("no recording", key="k" * 64), _LateRow("late")],
    ids=lambda e: type(e).__name__,
)
def test_lookahead_and_cache_miss_are_marked_integrity(exc: Exception) -> None:
    error = _fail(exc)
    assert error.integrity is True
    assert error.kind == type(exc).__name__  # the record is otherwise unchanged


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("vendor down"),
        DataError("blocked"),
        LLMError("timeout"),
        BudgetExceeded("over", scope="daily_usd", limit=5.0, spent=5.1),
    ],
    ids=lambda e: type(e).__name__,
)
def test_ordinary_failures_are_not_integrity(exc: Exception) -> None:
    assert _fail(exc).integrity is False


def test_the_integrity_kinds() -> None:
    assert (LookAheadError, CacheMiss) == INTEGRITY_ERRORS


def test_a_record_without_the_flag_still_validates() -> None:
    """Checkpoints written before the flag existed load unchanged (default False)."""
    error = NodeError(node="news", symbol="AAA", as_of=DAY, kind="RuntimeError", message="x")
    assert error.integrity is False


def test_integrity_errors_keeps_only_the_marked_ones_in_order() -> None:
    plain = node_error("news", "AAA")
    late = node_error("quant", "BBB").model_copy(update={"integrity": True})
    miss = node_error("director", "CCC").model_copy(update={"integrity": True})
    errors = [plain, late, node_error("prepare"), miss]
    assert integrity_errors(errors) == (late, miss)
    assert integrity_errors([plain]) == ()
    assert integrity_errors([]) == ()
    assert integrity_errors(None) == ()


def test_in_the_graph_a_cache_miss_drops_its_symbol_and_is_found() -> None:
    spy = Spy(
        hooks={
            ("BBB", "news"): raiser(CacheMiss("no recording", key="a" * 64)),
            ("CCC", "quant"): raiser(RuntimeError("vendor down")),
        }
    )
    out: dict[str, Any] = build_research_graph(spy.team(), max_concurrency=3).invoke(
        {"as_of": DAY, "research_queue": SYMBOLS}
    )
    assert failed_symbols(out["errors"]) == ("BBB", "CCC")  # the policy is unchanged
    assert set(out["theses"]) == {"AAA"}
    [found] = integrity_errors(out["errors"])
    assert (found.symbol, found.node, found.kind) == ("BBB", "news", "CacheMiss")
