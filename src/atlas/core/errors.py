"""Custom exceptions for Atlas.

Every Atlas error subclasses `AtlasError`. The kinds are deliberately siblings, not a deeper
tree: a handler that tolerates one kind (say bad vendor data) must never swallow another (say
a look-ahead bug). A kind may have refinements of itself (`BudgetExceeded`,
`CacheMiss` and `LLMTransientError` are `LLMError`s), so a caller that fails safe on the
kind covers them.
"""


class AtlasError(Exception):
    """Base class for every error raised by Atlas code."""


class ConfigError(AtlasError):
    """The configuration is missing, malformed or violates a validation rule."""


class LookAheadError(AtlasError):
    """Code asked for data that was not yet known at the decision's `as_of` cutoff."""


class DataError(AtlasError):
    """Input data is missing, malformed, stale or fails a quality check."""


class LLMError(AtlasError):
    """An LLM call failed, exceeded its budget or returned output that failed validation."""


class BudgetExceeded(LLMError):
    """An LLM call was refused because it would exceed a budget; callers fail safe (no new
    research). `scope` names the budget (`daily_usd`, `per_run_usd`,
    `per_agent_call_max_tokens`); `limit` and `spent` are in USD, or in tokens for the token
    cap (`spent` is then the tokens asked for)."""

    def __init__(self, message: str, *, scope: str, limit: float, spent: float) -> None:
        super().__init__(message)
        self.scope = scope
        self.limit = limit
        self.spent = spent


class LLMTransientError(LLMError):
    """An LLM call failed in a way worth retrying (rate limit, 5xx, timeout, dropped
    connection). `retry_after` is the server's requested wait in seconds, if it gave one."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class CacheMiss(LLMError):
    """A replay-only LLM call found no recorded answer and was refused: replay mode never
    calls a provider. `key` is the cache key that was looked up (see `atlas.llm.cache`)."""

    def __init__(self, message: str, *, key: str) -> None:
        super().__init__(message)
        self.key = key


class BacktestError(AtlasError):
    """A backtest reached a state it cannot continue from (for example the book lost its
    whole NAV in one session)."""


class RiskLimitError(AtlasError):
    """An action would breach a hard risk limit; the action must not go ahead."""


class ComplianceError(AtlasError):
    """An action violates a compliance rule (restricted list, locate, wash sale, ...)."""
