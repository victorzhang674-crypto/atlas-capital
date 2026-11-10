"""Retries with exponential backoff on transient provider failures (brief: Retries)."""

from collections.abc import Callable

import pytest

from atlas.core.config import AtlasConfig, LLMConfig, LLMRetry
from atlas.core.errors import LLMError, LLMTransientError
from atlas.llm import FakeLLM, LLMGateway
from atlas.llm.schemas import Message

MakeGateway = Callable[..., LLMGateway]


def test_transient_failures_are_retried_with_doubling_backoff(
    make_gateway: MakeGateway, sleeps: list[float]
) -> None:
    fake = FakeLLM()
    fake.add(None, None, LLMTransientError("429"), LLMTransientError("503"), "ok")
    result = make_gateway(fake).complete([Message.user("hi")])
    assert result.content == "ok"
    assert len(fake.calls) == 3
    assert sleeps == [1.0, 2.0]  # llm.retry.backoff_seconds, doubled
    assert result.attempts == 1  # one billed response; failed attempts returned nothing


def test_retry_after_wins_when_longer(make_gateway: MakeGateway, sleeps: list[float]) -> None:
    fake = FakeLLM()
    fake.add(
        None,
        None,
        LLMTransientError("429", retry_after=7.5),
        LLMTransientError("429", retry_after=0.1),
        "ok",
    )
    make_gateway(fake).complete([Message.user("hi")])
    assert sleeps == [7.5, 2.0]  # the server's wait when longer, else our backoff


def test_backoff_is_capped(make_gateway: MakeGateway, sleeps: list[float]) -> None:
    config = AtlasConfig(
        llm=LLMConfig(retry=LLMRetry(max_retries=5, backoff_seconds=4.0, max_backoff_seconds=10.0))
    )
    fake = FakeLLM()
    fake.add(None, None, *[LLMTransientError("503")] * 5, "ok")
    make_gateway(fake, config).complete([Message.user("hi")])
    assert sleeps == [4.0, 8.0, 10.0, 10.0, 10.0]


def test_exhausted_retries_raise_llm_error(make_gateway: MakeGateway, sleeps: list[float]) -> None:
    fake = FakeLLM(default=LLMTransientError("503 overloaded"))
    with pytest.raises(LLMError, match=r"failed after 4 attempts.*503 overloaded") as excinfo:
        make_gateway(fake).complete([Message.user("hi")])
    assert len(fake.calls) == 4  # 1 try + llm.retry.max_retries (3)
    assert sleeps == [1.0, 2.0, 4.0]  # no sleep after the last failure
    assert not isinstance(excinfo.value, LLMTransientError)
    assert isinstance(excinfo.value.__cause__, LLMTransientError)


def test_no_retries_when_configured_off(make_gateway: MakeGateway, sleeps: list[float]) -> None:
    config = AtlasConfig(llm=LLMConfig(retry=LLMRetry(max_retries=0)))
    fake = FakeLLM(default=LLMTransientError("503"))
    with pytest.raises(LLMError, match="failed after 1 attempt"):
        make_gateway(fake, config).complete([Message.user("hi")])
    assert len(fake.calls) == 1
    assert sleeps == []


def test_fatal_errors_are_not_retried(make_gateway: MakeGateway, sleeps: list[float]) -> None:
    fake = FakeLLM(default=LLMError("401 unauthorized"))
    with pytest.raises(LLMError, match="401 unauthorized"):
        make_gateway(fake).complete([Message.user("hi")])
    assert len(fake.calls) == 1
    assert sleeps == []


def test_unexpected_exceptions_propagate(make_gateway: MakeGateway) -> None:
    fake = FakeLLM(default=RuntimeError("bug in a provider"))
    with pytest.raises(RuntimeError, match="bug in a provider"):
        make_gateway(fake).complete([Message.user("hi")])
    assert len(fake.calls) == 1


def test_latency_includes_the_retries(make_gateway: MakeGateway) -> None:
    fake = FakeLLM()
    fake.add(None, None, LLMTransientError("429"), "ok")
    # The step timer is read once at the start and once at the end of the call.
    assert make_gateway(fake).complete([Message.user("hi")]).latency_ms == pytest.approx(250.0)


# --- Audit (mutation survivors) ------------------------------------------------------------


def test_missing_retry_after_adds_no_wait(make_gateway: MakeGateway, sleeps: list[float]) -> None:
    config = AtlasConfig(llm=LLMConfig(retry=LLMRetry(backoff_seconds=0.25)))
    fake = FakeLLM()
    fake.add(None, None, LLMTransientError("503"), LLMTransientError("503", retry_after=0.0), "ok")
    make_gateway(fake, config).complete([Message.user("hi")])
    assert sleeps == [0.25, 0.5]  # only our backoff: no hidden floor


def test_error_message_counts_attempts_in_plain_words(make_gateway: MakeGateway) -> None:
    single = AtlasConfig(llm=LLMConfig(retry=LLMRetry(max_retries=0)))
    with pytest.raises(LLMError, match=r"fake call to deepseek-flash failed after 1 attempt: 503"):
        make_gateway(FakeLLM(default=LLMTransientError("503")), single).complete(
            [Message.user("hi")]
        )
    double = AtlasConfig(llm=LLMConfig(retry=LLMRetry(max_retries=1)))
    with pytest.raises(LLMError, match=r"failed after 2 attempts: 503"):
        make_gateway(FakeLLM(default=LLMTransientError("503")), double).complete(
            [Message.user("hi")]
        )
