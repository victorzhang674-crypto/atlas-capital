"""Custom exceptions for Atlas.

Every Atlas error subclasses `AtlasError`. The specific errors are deliberately siblings, not
a deeper tree: a handler that tolerates one kind (say bad vendor data) must never swallow
another (say a look-ahead bug).
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


class RiskLimitError(AtlasError):
    """An action would breach a hard risk limit; the action must not go ahead."""


class ComplianceError(AtlasError):
    """An action violates a compliance rule (restricted list, locate, wash sale, ...)."""
