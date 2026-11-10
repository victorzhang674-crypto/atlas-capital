"""Shared helpers of the LLM gateway tests: fixed instants, a fixed clock, a step timer."""

from datetime import UTC, date, datetime

OFF_PEAK = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
"""A Saturday noon UTC: DeepSeek's off-peak (base) rates apply."""

PEAK = datetime(2026, 11, 9, 2, 30, tzinfo=UTC)
"""A Monday 02:30 UTC: inside DeepSeek's 01:00-04:00 peak window."""


class FixedClock:
    """A wall clock that stands still at `ts` until a test moves it."""

    def __init__(self, ts: datetime = OFF_PEAK) -> None:
        self.ts = ts

    def now(self) -> datetime:
        return self.ts

    def today(self) -> date:
        return self.ts.date()


class StepTimer:
    """A monotonic timer that advances `step` seconds on every reading."""

    def __init__(self, step: float = 0.25) -> None:
        self.step = step
        self.t = 0.0

    def __call__(self) -> float:
        self.t += self.step
        return self.t
