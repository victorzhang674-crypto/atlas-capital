"""Every call is logged (acceptance criterion): one `llm.call` line per billed response."""

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from atlas.core.errors import LLMTransientError
from atlas.core.logging import configure_logging, reset_logging
from atlas.core.run_context import run_context
from atlas.llm import FakeLLM, FakeReply, LLMGateway
from atlas.llm.schemas import Message, Usage

MakeGateway = Callable[..., LLMGateway]


def test_each_billed_response_logs_tokens_and_cost(make_gateway: MakeGateway) -> None:
    reply = FakeReply(
        content="ok", usage=Usage(input_tokens=1000, cached_input_tokens=4000, output_tokens=500)
    )
    with capture_logs() as logs:
        result = make_gateway(FakeLLM(default=reply)).complete(
            [Message.user("hi")], tier="deep", metadata={"agent": "analyst", "step": "draft"}
        )
    (line,) = [entry for entry in logs if entry["event"] == "llm.call"]
    assert line["log_level"] == "info"
    assert (line["agent"], line["step"], line["tier"]) == ("analyst", "draft", "deep")
    assert (line["provider"], line["model"]) == ("fake", "deepseek-v4-pro")
    assert (line["input_tokens"], line["cached_input_tokens"], line["output_tokens"]) == (
        1000,
        4000,
        500,
    )
    assert line["cost_usd"] == result.cost_usd == pytest.approx(1738e-6, rel=1e-12)
    assert line["latency_ms"] == pytest.approx(250.0)
    assert (line["attempt"], line["status"]) == (1, "ok")


def test_retries_are_logged_as_warnings(make_gateway: MakeGateway) -> None:
    fake = FakeLLM()
    fake.add(None, None, LLMTransientError("HTTP 429: slow down", retry_after=3.0), "ok")
    with capture_logs() as logs:
        make_gateway(fake).complete([Message.user("hi")])
    (retry,) = [entry for entry in logs if entry["event"] == "llm.retry"]
    assert retry["log_level"] == "warning"
    assert (retry["attempt"], retry["wait_seconds"]) == (1, 3.0)
    assert retry["model"] == "deepseek-flash"
    assert "429" in retry["error"]
    assert len([entry for entry in logs if entry["event"] == "llm.call"]) == 1


@pytest.fixture
def log_file(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "logs" / "atlas.jsonl"
    configure_logging(log_file=path, console_level="CRITICAL")
    yield path
    reset_logging()


def test_log_line_carries_the_run_id(make_gateway: MakeGateway, log_file: Path) -> None:
    gateway = make_gateway(FakeLLM(default="ok"))
    with run_context() as run:
        gateway.complete([Message.user("hi")], metadata={"agent": "analyst"})
    lines = [json.loads(text) for text in log_file.read_text().splitlines()]
    (call,) = [line for line in lines if line["event"] == "llm.call"]
    assert call["run_id"] == run.run_id
    assert call["agent"] == "analyst"
    assert call["cost_usd"] > 0


def test_retry_log_names_the_provider(make_gateway: MakeGateway) -> None:  # audit
    fake = FakeLLM()
    fake.add(None, None, LLMTransientError("503"), "ok")
    with capture_logs() as logs:
        make_gateway(fake).complete([Message.user("hi")])
    (retry,) = [entry for entry in logs if entry["event"] == "llm.retry"]
    assert retry["provider"] == "fake"


def test_a_refused_call_is_logged(make_gateway: MakeGateway) -> None:  # audit
    from atlas.core.errors import BudgetExceeded

    with capture_logs() as logs, pytest.raises(BudgetExceeded):
        make_gateway(FakeLLM(default="ok")).complete(
            [Message.user("hi")], max_tokens=9000, metadata={"agent": "analyst"}
        )
    (refused,) = [entry for entry in logs if entry["event"] == "llm.budget_exceeded"]
    assert refused["log_level"] == "warning"
    assert (refused["scope"], refused["limit"], refused["spent"]) == (
        "per_agent_call_max_tokens",
        8000,
        9000,
    )
    assert (refused["agent"], refused["model"]) == ("analyst", "deepseek-flash")
    assert [entry for entry in logs if entry["event"] == "llm.call"] == []
