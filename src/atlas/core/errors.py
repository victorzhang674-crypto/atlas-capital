"""Custom exceptions for Atlas. Stage 03 extends this hierarchy."""


class AtlasError(Exception):
    """Base class for every error raised by Atlas code."""


class ConfigError(AtlasError):
    """The configuration is missing, malformed or violates a validation rule."""
