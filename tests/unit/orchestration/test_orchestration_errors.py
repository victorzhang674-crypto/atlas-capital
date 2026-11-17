"""Stage 28 Task 3: the error policy (`NodeError`, `guarded`, `failed_symbols`)."""

import threading
from typing import Any

import pytest
from graph_world import DAY, node_error
from langgraph.errors import GraphInterrupt
from pydantic import ValidationError
from structlog.testing import capture_logs

from atlas.core.errors import BudgetExceeded, LookAheadError
from atlas.orchestration.errors import MAX_MESSAGE_CHARS, NodeError, failed_symbols, guarded

STATE: dict[str, Any] = {"symbol": "AAA", "as_of": DAY}


def test_node_error_is_a_frozen_contract() -> None:
    error = node_error("news", "AAA")
    with pytest.raises(ValidationError):
        error.node = "other"  # type: ignore[misc]
    assert error.symbol == "AAA" and error.as_of == DAY


@pytest.mark.parametrize(
    "bad",
    [
        {"node": ""},
        {"kind": " "},
        {"message": "x" * (MAX_MESSAGE_CHARS + 1)},
        {"symbol": "not a symbol!"},
        {"extra": 1},
    ],
)
def test_node_error_validation(bad: dict[str, Any]) -> None:
    fields: dict[str, Any] = {
        "node": "news",
        "symbol": "AAA",
        "as_of": DAY,
        "kind": "RuntimeError",
        "message": "boom",
    }
    with pytest.raises(ValidationError):
        NodeError(**{**fields, **bad})


def test_guarded_returns_the_work_update_on_success() -> None:
    node = guarded("news", lambda state: {"answer": state["symbol"]})
    assert node(STATE) == {"answer": "AAA"}


def test_guarded_turns_an_exception_into_a_node_error() -> None:
    def work(state: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("vendor down")

    with capture_logs() as logs:
        update = guarded("news", work)(STATE)
    assert update == {
        "errors": [
            NodeError(
                node="news", symbol="AAA", as_of=DAY, kind="RuntimeError", message="vendor down"
            )
        ]
    }
    [record] = [r for r in logs if r["event"] == "orchestration_node_failed"]
    assert record["log_level"] == "error"
    assert record["node"] == "news" and record["symbol"] == "AAA"
    assert record["kind"] == "RuntimeError"
    assert record["exc_info"] is True


def test_a_fund_level_node_error_has_no_symbol() -> None:
    def work(state: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("bad")

    [error] = guarded("prepare", work)({"as_of": DAY})["errors"]
    assert error.symbol is None and error.node == "prepare" and error.kind == "ValueError"


@pytest.mark.parametrize(
    "exc",
    [
        LookAheadError("future row"),
        BudgetExceeded("over", scope="daily_usd", limit=5.0, spent=5.1),
        KeyError("missing"),
    ],
)
def test_every_exception_kind_is_recorded(exc: Exception) -> None:
    def work(state: dict[str, Any]) -> dict[str, Any]:
        raise exc

    [error] = guarded("news", work)(STATE)["errors"]
    assert error.kind == type(exc).__name__


def test_a_long_message_is_cut() -> None:
    def work(state: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("y" * 2000)

    [error] = guarded("news", work)(STATE)["errors"]
    assert len(error.message) == MAX_MESSAGE_CHARS
    assert error.message.endswith("...")


def test_an_empty_message_is_kept_empty() -> None:
    def work(state: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError

    [error] = guarded("news", work)(STATE)["errors"]
    assert error.message == "" and error.kind == "RuntimeError"


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(1), GraphInterrupt()])
def test_stops_and_control_flow_propagate(exc: BaseException) -> None:
    def work(state: dict[str, Any]) -> dict[str, Any]:
        raise exc

    with pytest.raises(type(exc)):
        guarded("news", work)(STATE)


def test_the_limiter_is_held_only_during_the_work() -> None:
    limiter = threading.BoundedSemaphore(1)
    seen: list[bool] = []

    def work(state: dict[str, Any]) -> dict[str, Any]:
        seen.append(limiter.acquire(blocking=False))  # the slot is taken while working
        return {}

    def failing(state: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("x")

    guarded("news", work, limiter=limiter)(STATE)
    guarded("news", failing, limiter=limiter)(STATE)
    assert seen == [False]
    assert limiter.acquire(blocking=False)  # released after success and after failure


def test_failed_symbols_lists_each_symbol_once_in_order() -> None:
    errors = [
        node_error("news", "BBB"),
        node_error("prepare"),
        node_error("quant", "AAA"),
        node_error("director", "BBB"),
    ]
    assert failed_symbols(errors) == ("AAA", "BBB")
    assert failed_symbols([]) == ()
    assert failed_symbols(None) == ()
