"""`LLMGateway`: the one door every agent uses to call a model (spec §3, §11; Stage 24)."""

import math
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC

from pydantic import BaseModel, ValidationError
from ulid import ULID

from atlas.core.clock import Clock, LiveClock
from atlas.core.config import AtlasConfig, ModelPrice
from atlas.core.errors import (
    BudgetExceeded,
    CacheMiss,
    ConfigError,
    LLMError,
    LLMTransientError,
)
from atlas.core.logging import get_logger
from atlas.core.run_context import current_run_context
from atlas.core.storage import Lake, get_database
from atlas.core.types.base import FrozenDict
from atlas.llm.budget import BudgetGuard
from atlas.llm.cache import CacheEntry, CacheMode, LLMCache, StoredResult, cache_key, request_json
from atlas.llm.cost import CostLedger, UsageRecord, cost_usd
from atlas.llm.hygiene import with_data_policy
from atlas.llm.schemas import (
    LLMProvider,
    LLMResult,
    Message,
    ProviderRequest,
    ProviderResponse,
    Tier,
    ToolSpec,
    Usage,
)

MAX_TEMPERATURE = 2.0
"""Largest sampling temperature a call may ask for."""

REPAIR_RETRIES = 1
"""Repair retries after an answer that fails schema validation (stage protocol §4: once)."""

MAX_ERROR_CHARS = 2000
"""A validation error is cut to this many characters in the repair message and in errors."""

KEY_LOCK_STRIPES = 64
"""Number of locks that serialise calls with the same cache key (keys share a stripe by
their leading hex digits)."""

_log = get_logger(__name__)

_FENCE_RE = re.compile(r"^\s*```[A-Za-z]*[ \t]*\n?(.*?)\s*```\s*$", re.DOTALL)


def _parse(schema: type[BaseModel], content: str) -> BaseModel:
    """`content` validated as JSON of `schema`; one surrounding markdown code fence is
    ignored. Raises pydantic's `ValidationError` for bad JSON or a schema mismatch."""
    fenced = _FENCE_RE.match(content)
    return schema.model_validate_json(fenced.group(1) if fenced else content.strip())


def _error_text(exc: ValidationError) -> str:
    """The validation errors as short text: no input echo, no documentation URLs."""
    lines = [
        f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
        for error in exc.errors(include_url=False, include_input=False)
    ]
    return "\n".join(lines)[:MAX_ERROR_CHARS]


def _repair_messages(
    schema: type[BaseModel], response: ProviderResponse, error: str
) -> tuple[Message, ...]:
    """The turns added for a repair retry: the rejected answer (if it had any text) and a
    user message that shows the validation error."""
    ask = Message.user(
        f"Your previous answer is not valid JSON for the required schema {schema.__name__}.\n"
        f"Validation error:\n{error}\n"
        "Answer again with one corrected JSON object and nothing else."
    )
    if not response.content.strip():
        return (ask,)
    return (Message.assistant(response.content), ask)


class LLMGateway:
    """Provider-agnostic access to tiered models.

    For every call the gateway picks the tier's model, adds the data-policy system text,
    refuses the call if a budget would be exceeded, retries transient failures, validates
    structured output (one repair retry), costs each provider response and writes it to
    `ledger` (default: a new in-memory `CostLedger`).

    With a `cache` the gateway replays recorded answers and records new ones as `cache_mode`
    says (default: `config.llm.cache.mode`; a gateway built with no `cache` never caches).
    A hit costs nothing: no budget is checked, no provider is called, the result has
    `cache_hit = True` and the ledger gets a `cache_hit` row with cost 0.

    `clock` is the wall clock used for billing time (default `LiveClock()`; prices and the
    daily budget follow real UTC time, not simulated time), `sleep` waits between retries
    and `timer` measures latency in seconds.
    """

    def __init__(
        self,
        provider: LLMProvider,
        config: AtlasConfig,
        *,
        ledger: CostLedger | None = None,
        clock: Clock | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timer: Callable[[], float] = time.perf_counter,
        cache: LLMCache | None = None,
        cache_mode: CacheMode | None = None,
    ) -> None:
        mode: CacheMode = cache_mode or (config.llm.cache.mode if cache is not None else "off")
        if mode != "off" and cache is None:
            raise ConfigError(f"cache mode {mode!r} needs a cache: pass `cache=LLMCache(...)`")
        self._cache = cache
        self._cache_mode: CacheMode = mode
        self._key_locks = tuple(threading.Lock() for _ in range(KEY_LOCK_STRIPES))
        self._recorded: set[str] = set()
        self._provider = provider
        self._llm = config.llm
        self._ledger = ledger if ledger is not None else CostLedger()
        self._budget = BudgetGuard(config.llm.budgets, self._ledger)
        self._clock: Clock = clock or LiveClock()
        self._sleep = sleep
        self._timer = timer

    @property
    def provider(self) -> LLMProvider:
        """The provider behind this gateway."""
        return self._provider

    @property
    def cache(self) -> LLMCache | None:
        """The record/replay cache behind this gateway, if any."""
        return self._cache

    @property
    def cache_mode(self) -> CacheMode:
        """How the cache is used: `off` when there is none or it is switched off."""
        return self._cache_mode

    @property
    def ledger(self) -> CostLedger:
        """The usage ledger this gateway writes to."""
        return self._ledger

    def complete(
        self,
        messages: Sequence[Message],
        *,
        tier: Tier = "quick",
        schema: type[BaseModel] | None = None,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> LLMResult:
        """Call the `tier` model with `messages` and return its costed answer.

        `temperature` is in [0, 2] and is a hint (providers that reject sampling parameters
        drop it). `max_tokens` caps the output tokens of one provider response (default and
        upper limit: the configured `per_agent_call_max_tokens`). `metadata` labels the call;
        `agent` and `step` key `FakeLLM` scripts and the usage ledger.

        With `schema`, the answer must be JSON of that Pydantic model: it is validated, and
        after a failure the model is asked once more with the validation error shown.

        Raises `BudgetExceeded` when the token cap, the daily budget or the run's budget
        would be exceeded (nothing is sent), `LLMError` when the provider fails for good or
        the output is still invalid after the repair retry, and `ConfigError` when the
        model has no price. All three mean: fail safe, no new research.
        """
        if not messages:
            raise ValueError("messages must not be empty")
        if not (math.isfinite(temperature) and 0.0 <= temperature <= MAX_TEMPERATURE):
            raise ValueError(f"temperature must be in [0, {MAX_TEMPERATURE}], got {temperature}")
        if max_tokens is not None and max_tokens < 1:
            raise ValueError(f"max_tokens must be at least 1, got {max_tokens}")
        model = self._llm.model_for(tier)
        price = self._llm.prices.get(model)
        if price is None:
            raise ConfigError(f"model {model!r} has no price in llm.prices; the call is not made")
        request = ProviderRequest(
            model=model,
            tier=tier,
            messages=with_data_policy(messages),
            max_tokens=max_tokens or self._llm.budgets.per_agent_call_max_tokens,
            timeout_seconds=self._llm.request_timeout_seconds,
            schema=schema,
            tools=tuple(tools or ()),
            temperature=temperature,
            thinking=self._llm.thinking.deep if tier == "deep" else self._llm.thinking.quick,
            metadata=FrozenDict(dict(metadata or {})),
        )
        try:
            return self._cached(request, price)
        except BudgetExceeded as exc:
            _log.warning(
                "llm.budget_exceeded",
                agent=request.metadata.get("agent"),
                step=request.metadata.get("step"),
                tier=tier,
                model=model,
                scope=exc.scope,
                limit=exc.limit,
                spent=exc.spent,
            )
            raise

    def _cached(self, request: ProviderRequest, price: ModelPrice) -> LLMResult:
        """`_run` behind the record/replay cache (see `cache_mode`).

        Calls with the same key are serialised, so concurrent identical misses make one
        provider call and all get the recorded answer. In `record` mode each key is re-recorded
        once per gateway; later identical calls replay that fresh recording. Both keep the
        spec's promise (§3 principle 3) that a replay gives the results the recording run saw.
        """
        cache, mode = self._cache, self._cache_mode
        if cache is None or mode == "off":
            return self._run(request, price)
        provider = self._provider.name
        key = cache_key(request, provider=provider)
        with self._key_locks[int(key[:8], 16) % KEY_LOCK_STRIPES]:
            return self._cached_locked(request, price, cache, mode, provider, key)

    def _cached_locked(
        self,
        request: ProviderRequest,
        price: ModelPrice,
        cache: LLMCache,
        mode: CacheMode,
        provider: str,
        key: str,
    ) -> LLMResult:
        """`_cached` with the key's lock held."""
        if mode != "record" or key in self._recorded:
            started = self._timer()
            entry = cache.get(key)
            if entry is not None:
                return self._replay(request, entry, started)
            if mode == "replay_only":
                _log.warning(
                    "llm.cache_miss",
                    agent=request.metadata.get("agent"),
                    step=request.metadata.get("step"),
                    key=key,
                )
                raise CacheMiss(
                    f"replay_only: no recorded answer for key {key} (agent="
                    f"{request.metadata.get('agent')!r}, step={request.metadata.get('step')!r}); "
                    "the prompt or the data changed, or the call was never recorded "
                    "(record it with llm.cache.mode read_write or record)",
                    key=key,
                )
        result = self._run(request, price)
        cache.put(
            CacheEntry.create(
                key=key,
                request_json=request_json(request, provider=provider),
                provider=provider,
                model=request.model,
                result=StoredResult(
                    content=result.content,
                    tool_calls=result.tool_calls,
                    finish_reason=result.finish_reason,
                    usage=result.usage,
                    cost_usd=result.cost_usd,
                    attempts=result.attempts,
                ),
                created_at=self._clock.now().astimezone(UTC),
            ),
            replace=mode == "record",
        )
        if mode == "record":
            self._recorded.add(key)
        return result

    def _replay(self, request: ProviderRequest, entry: CacheEntry, started: float) -> LLMResult:
        """The stored answer of `entry` as a free result, with a `cache_hit` ledger row.

        Raises `LLMError` when the entry is corrupt or no longer fits the requested schema:
        a cache never guesses, and never falls back to a provider call silently."""
        stored = entry.result()
        schema, parsed = request.schema, None
        if schema is not None and not stored.tool_calls:
            try:
                parsed = _parse(schema, stored.content)
            except ValidationError as exc:
                raise LLMError(
                    f"cached answer {entry.key} does not match schema {schema.__name__}: "
                    f"{_error_text(exc)}"
                ) from exc
        now = self._clock.now().astimezone(UTC)
        run = current_run_context()
        latency_ms = (self._timer() - started) * 1000.0
        record = UsageRecord(
            call_id=str(ULID()),
            ts=now,
            day=now.date(),
            run_id=run.run_id if run is not None else None,
            agent=request.metadata.get("agent"),
            step=request.metadata.get("step"),
            tier=request.tier,
            provider=self._provider.name,
            model=request.model,
            cost_usd=0.0,
            latency_ms=latency_ms,
            cache_hit=True,
        )
        self._ledger.record(record)
        _log.info(
            "llm.cache_hit",
            agent=record.agent,
            step=record.step,
            tier=record.tier,
            model=record.model,
            key=entry.key,
            recorded_at=entry.created_at.isoformat(),
            saved_usd=stored.cost_usd,
            latency_ms=latency_ms,
        )
        return LLMResult(
            content=stored.content,
            parsed=parsed,
            usage=Usage(),
            cost_usd=0.0,
            model=request.model,
            latency_ms=latency_ms,
            cache_hit=True,
            tool_calls=stored.tool_calls,
            finish_reason=stored.finish_reason,
            attempts=0,
        )

    def _run(self, request: ProviderRequest, price: ModelPrice) -> LLMResult:
        """Budget-check, send, validate, cost and record `request`; see `complete`."""
        model, tier, schema = request.model, request.tier, request.schema
        self._budget.check_max_tokens(request.max_tokens)
        run = current_run_context()
        run_id = run.run_id if run is not None else None
        started = last = self._timer()
        usage, cost, attempts, parsed = Usage(), 0.0, 0, None
        while True:
            now = self._clock.now().astimezone(UTC)
            estimate = self._budget.estimate_usd(request, price, now)
            failure: LLMError | None = None
            with self._budget.reserve(estimate, day=now.date(), run_id=run_id):
                response = self._send(request)
                attempts += 1
                billed_at = self._clock.now().astimezone(UTC)
                response_cost = cost_usd(response.usage, price, billed_at)
                usage += response.usage
                cost += response_cost
                error: str | None = None
                if schema is not None and not response.tool_calls:
                    try:
                        parsed = _parse(schema, response.content)
                    except ValidationError as exc:
                        error = _error_text(exc)
                        if attempts > REPAIR_RETRIES:
                            failure = LLMError(
                                f"{model} output does not match schema {schema.__name__} after "
                                f"{REPAIR_RETRIES} repair retry: {error}"
                            )
                            failure.__cause__ = exc
                finished = self._timer()
                record = UsageRecord(
                    call_id=str(ULID()),
                    ts=billed_at,
                    day=billed_at.date(),
                    run_id=run_id,
                    agent=request.metadata.get("agent"),
                    step=request.metadata.get("step"),
                    tier=tier,
                    provider=self._provider.name,
                    model=model,
                    input_tokens=response.usage.input_tokens,
                    cached_input_tokens=response.usage.cached_input_tokens,
                    cache_write_tokens=response.usage.cache_write_tokens,
                    output_tokens=response.usage.output_tokens,
                    reasoning_tokens=response.usage.reasoning_tokens,
                    cost_usd=response_cost,
                    latency_ms=(finished - last) * 1000.0,
                    attempt=attempts,
                    status="ok" if error is None else "invalid_output",
                )
                last = finished
                self._ledger.record(record)
                _log.info(
                    "llm.call",
                    agent=record.agent,
                    step=record.step,
                    tier=tier,
                    provider=record.provider,
                    model=model,
                    input_tokens=record.input_tokens,
                    cached_input_tokens=record.cached_input_tokens,
                    output_tokens=record.output_tokens,
                    cost_usd=record.cost_usd,
                    latency_ms=record.latency_ms,
                    attempt=attempts,
                    status=record.status,
                )
            if failure is not None:
                raise failure
            if error is None or schema is None:
                break
            request = replace(
                request, messages=(*request.messages, *_repair_messages(schema, response, error))
            )
        return LLMResult(
            content=response.content,
            parsed=parsed,
            usage=usage,
            cost_usd=cost,
            model=model,
            latency_ms=(last - started) * 1000.0,
            tool_calls=response.tool_calls,
            finish_reason=response.finish_reason,
            attempts=attempts,
        )

    def _send(self, request: ProviderRequest) -> ProviderResponse:
        """One provider answer, retrying transient failures with exponential backoff.

        Waits `backoff_seconds * 2**(n-1)` seconds (at most `max_backoff_seconds`, at least
        the server's `retry_after`) before retry `n`. After `max_retries` retries the last
        failure is raised as a plain `LLMError`; other errors are never retried.
        """
        retry = self._llm.retry
        tries = retry.max_retries + 1
        for attempt in range(1, tries + 1):
            try:
                return self._provider.complete(request)
            except LLMTransientError as exc:
                if attempt == tries:
                    raise LLMError(
                        f"{self._provider.name} call to {request.model} failed after {tries} "
                        f"attempt{'s' if tries > 1 else ''}: {exc}"
                    ) from exc
                wait = min(retry.backoff_seconds * 2 ** (attempt - 1), retry.max_backoff_seconds)
                wait = max(wait, exc.retry_after or 0.0)
                _log.warning(
                    "llm.retry",
                    provider=self._provider.name,
                    model=request.model,
                    attempt=attempt,
                    wait_seconds=wait,
                    error=str(exc),
                )
                self._sleep(wait)
        raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover


def build_provider(config: AtlasConfig) -> LLMProvider:
    """The provider `config.llm.provider` names, with its key from `config.secrets`.

    Raises `ConfigError` naming the missing environment variable when the key is not set.
    """
    if config.llm.provider == "anthropic":
        from atlas.llm.providers.anthropic import AnthropicProvider

        if config.secrets.anthropic_api_key is None:
            raise ConfigError(
                "llm.provider is 'anthropic' but ANTHROPIC_API_KEY is not set (env var or .env)"
            )
        return AnthropicProvider(config.secrets.anthropic_api_key)
    from atlas.llm.providers.openai_compat import OpenAICompatibleProvider

    if config.secrets.deepseek_api_key is None:
        raise ConfigError(
            "llm.provider is 'openai_compatible' but DEEPSEEK_API_KEY is not set (env var or .env)"
        )
    return OpenAICompatibleProvider(config.llm.base_url, config.secrets.deepseek_api_key)


def build_gateway(config: AtlasConfig, *, lake: Lake | None = None) -> LLMGateway:
    """A gateway on the configured provider (see `build_provider`).

    With `lake`, usage rows are stored in that lake's DuckDB table `llm_usage` and recorded
    answers in `llm_cache` (the process-wide database of the lake), so budgets and replays
    work across sessions; without it both are in memory. `config.llm.cache.mode` says how the
    cache is used; `off` builds no cache.
    """
    database = get_database(lake) if lake is not None else None
    cache = LLMCache(database) if config.llm.cache.mode != "off" else None
    return LLMGateway(build_provider(config), config, ledger=CostLedger(database), cache=cache)
