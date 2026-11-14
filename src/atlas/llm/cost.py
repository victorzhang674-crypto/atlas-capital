"""Costing of LLM calls from the price table in config, and the usage ledger.

Prices are USD per 1M tokens. A call is priced at the UTC wall-clock instant it was made:
vendors bill in real time (DeepSeek's peak hours are real UTC hours), also when the call is
part of a backtest that simulates another date.
"""

import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from pydantic import Field

from atlas.core.config import ModelPrice, PriceWindow
from atlas.core.storage.db import Database
from atlas.core.types.base import AtlasModel
from atlas.llm.schemas import Tier, Usage

TOKENS_PER_PRICE_UNIT = 1_000_000
"""Prices are quoted per this many tokens."""

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class Rates:
    """The four rates in force at one instant, USD per 1M tokens, with no gaps: a model
    without a cache-read or cache-write price is charged its input rate for those tokens."""

    input: float
    cached_input: float
    cache_write: float
    output: float


def rates_at(price: ModelPrice, ts: datetime) -> Rates:
    """The rates of `price` at the timezone-aware instant `ts`.

    `ts` is converted to UTC; the first of `price.windows` whose weekday list holds that UTC
    weekday and whose `[start, end)` holds that UTC time of day replaces the base rates.
    """
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"naive datetime {ts!r}; pass a timezone-aware datetime")
    at = ts.astimezone(UTC)
    weekday, clock_time = _WEEKDAYS[at.weekday()], at.time()
    source: ModelPrice | PriceWindow = price
    for window in price.windows:
        if weekday in window.days and window.start <= clock_time < window.end:
            source = window
            break
    return Rates(
        input=source.input,
        cached_input=source.input if source.cached_input is None else source.cached_input,
        cache_write=source.input if source.cache_write is None else source.cache_write,
        output=source.output,
    )


def cost_usd(usage: Usage, price: ModelPrice, ts: datetime) -> float:
    """Cost in USD of `usage` at `price`, for a call billed at the timezone-aware instant
    `ts`. Reasoning tokens are already inside `output_tokens` and are not charged again."""
    rates = rates_at(price, ts)
    return (
        usage.input_tokens * rates.input
        + usage.cached_input_tokens * rates.cached_input
        + usage.cache_write_tokens * rates.cache_write
        + usage.output_tokens * rates.output
    ) / TOKENS_PER_PRICE_UNIT


_Count = Annotated[int, Field(ge=0)]


class UsageRecord(AtlasModel):
    """One billed provider response, as stored in `llm_usage`.

    `ts` is the UTC wall-clock instant the response was costed and `day` its UTC date (the
    daily budget's day). `run_id`, `agent` and `step` are `None` when the call had none.
    Token counts are whole tokens (`reasoning_tokens` are part of `output_tokens`);
    `cost_usd` is USD; `latency_ms` is milliseconds, retries of that response included.
    `attempt` is 1, or 2 for a repair retry. `status` is `ok`, or `invalid_output` when the
    answer failed schema validation (it was billed all the same).
    """

    call_id: Annotated[str, Field(min_length=1)]
    ts: datetime
    day: date
    run_id: str | None = None
    agent: str | None = None
    step: str | None = None
    tier: Tier
    provider: str
    model: str
    input_tokens: _Count = 0
    cached_input_tokens: _Count = 0
    cache_write_tokens: _Count = 0
    output_tokens: _Count = 0
    reasoning_tokens: _Count = 0
    cost_usd: Annotated[float, Field(ge=0)]
    latency_ms: Annotated[float, Field(ge=0)]
    cache_hit: bool = False
    attempt: Annotated[int, Field(ge=1)] = 1
    status: Literal["ok", "invalid_output"] = "ok"


class CostLedger:
    """The record of every billed LLM response, with spend totals per day, run and agent.

    With a `database` the rows live in its native DuckDB table `llm_usage` (the totals then
    include spend recorded by earlier sessions on the same lake); without one they live in
    memory, which is enough for unit tests and one-off scripts. Thread-safe. Totals are USD.
    """

    def __init__(self, database: Database | None = None) -> None:
        self._database = database
        self._lock = threading.Lock()
        self._records: list[UsageRecord] = []

    def record(self, record: UsageRecord) -> None:
        """Append one billed response."""
        if self._database is not None:
            self._database.insert_llm_usage([record.model_dump()])
            return
        with self._lock:
            self._records.append(record)

    def records(self) -> tuple[UsageRecord, ...]:
        """Every record, in the order written."""
        if self._database is None:
            with self._lock:
                return tuple(self._records)
        frame = self._database.query("SELECT * FROM llm_usage ORDER BY rowid")
        rows = frame.astype(object).where(frame.notna(), None).to_dict(orient="records")
        return tuple(UsageRecord.model_validate(row) for row in rows)

    def _total(self, where: str, params: list[object]) -> float:
        assert self._database is not None
        frame = self._database.query(
            f"SELECT coalesce(sum(cost_usd), 0.0) AS usd FROM llm_usage WHERE {where}", params
        )
        return float(frame["usd"].iloc[0])

    def cache_counts(self) -> tuple[int, int]:
        """`(hits, paid)`: the calls the record/replay cache answered, and the first provider
        responses of calls it did not (repair retries are not counted again). Hit rate is
        `hits / (hits + paid)`."""
        if self._database is not None:
            frame = self._database.query(
                "SELECT coalesce(sum(CASE WHEN cache_hit THEN 1 ELSE 0 END), 0) AS hits, "
                "coalesce(sum(CASE WHEN NOT cache_hit AND attempt = 1 THEN 1 ELSE 0 END), 0) "
                "AS paid FROM llm_usage"
            )
            return int(frame["hits"].iloc[0]), int(frame["paid"].iloc[0])
        records = self.records()
        hits = sum(r.cache_hit for r in records)
        return hits, sum(not r.cache_hit and r.attempt == 1 for r in records)

    def spent_on(self, day: date) -> float:
        """USD spent on the UTC calendar day `day`."""
        if self._database is not None:
            return self._total("day = ?", [day])
        return sum(r.cost_usd for r in self.records() if r.day == day)

    def spent_in_run(self, run_id: str | None) -> float:
        """USD spent by the run `run_id`; `None` totals the calls made outside any run."""
        if self._database is not None:
            if run_id is None:
                return self._total("run_id IS NULL", [])
            return self._total("run_id = ?", [run_id])
        return sum(r.cost_usd for r in self.records() if r.run_id == run_id)

    def spent_by_agent(self, agent: str, *, day: date | None = None) -> float:
        """USD spent by `agent`, on every day or only on the UTC day `day`."""
        if self._database is not None:
            if day is None:
                return self._total("agent = ?", [agent])
            return self._total("agent = ? AND day = ?", [agent, day])
        return sum(
            r.cost_usd for r in self.records() if r.agent == agent and (day is None or r.day == day)
        )
