"""Fixtures for the LLM gateway tests: a fixed wall clock, a counting timer, recorded sleeps."""

from collections.abc import Callable

import pytest
from llm_helpers import FixedClock, StepTimer

from atlas.core.config import AtlasConfig
from atlas.llm import FakeLLM, LLMGateway
from atlas.llm.schemas import LLMProvider


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def sleeps() -> list[float]:
    """Seconds the gateway asked to sleep, in order (nothing really sleeps)."""
    return []


@pytest.fixture
def cfg() -> AtlasConfig:
    return AtlasConfig()


@pytest.fixture
def make_gateway(
    cfg: AtlasConfig, clock: FixedClock, sleeps: list[float]
) -> Callable[..., LLMGateway]:
    """Build a gateway around a provider with the fixed clock, a step timer and fake sleeps."""

    def build(
        provider: LLMProvider, config: AtlasConfig | None = None, **kwargs: object
    ) -> LLMGateway:
        return LLMGateway(
            provider,
            config or cfg,
            clock=kwargs.pop("clock", clock),  # type: ignore[arg-type]
            sleep=sleeps.append,
            timer=StepTimer(),
            **kwargs,  # type: ignore[arg-type]
        )

    return build


@pytest.fixture
def fake() -> FakeLLM:
    return FakeLLM()
