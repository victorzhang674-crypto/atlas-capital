"""The LLM record/replay cache: identical requests get stored answers (spec §3.3, Stage 25).

A request's key is the SHA-256 of the canonical JSON of everything the model is asked (see
`key_payload`). A stored entry holds that JSON, the answer and the tokens and USD cost of the
call that produced it. The gateway decides when to look up and store (`CacheMode`).

Replay is only valid while prompts and data are unchanged: the key covers the exact messages
(so changed data changes the key) and the `prompt_version` label a prompt template sets (so a
template change that the messages cannot show is a version bump); nothing else voids a
recording. See `docs/llm-cache.md`.
"""

import hashlib
import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field, ValidationError

from atlas.core.audit import canonical_json
from atlas.core.config import LLMCacheMode
from atlas.core.errors import LLMError
from atlas.core.storage.db import Database
from atlas.core.types.base import AtlasModel, FrozenDict
from atlas.llm.schemas import ProviderRequest, ToolCall, Usage

CacheMode = LLMCacheMode
"""`off`, `read_write`, `replay_only` or `record` (see `LLMCacheConfig`)."""

KEY_VERSION = 2
"""Version of the key payload's shape: bump it if `key_payload` changes (every key changes)."""

PROMPT_VERSION_LABEL = "prompt_version"
"""The request metadata key that carries the prompt template version."""


def schema_fingerprint(schema: type[BaseModel]) -> str:
    """SHA-256 of `schema`'s JSON Schema: the "schema version" of the key. A changed field,
    type, default or description changes it, so nobody has to remember to bump a number."""
    return hashlib.sha256(canonical_json(schema.model_json_schema()).encode()).hexdigest()


def key_payload(request: ProviderRequest, *, provider: str) -> dict[str, Any]:
    """What the key hashes: everything the provider is sent that can change the answer — the
    provider, the model, the messages as sent, the schema name and fingerprint, the
    temperature (as a float: 1 and 1.0 are one value), the output token cap (an answer cut at
    one cap is not the answer to a larger one), the tools, the thinking mode — and the prompt
    template version (`request.metadata["prompt_version"]`, empty when absent).

    Not in it: the labels `agent` / `step` and the timeout. They do not change what the model
    is asked, so two agents with the same question share an answer.
    """
    schema = request.schema
    return {
        "key_version": KEY_VERSION,
        "provider": provider,
        "model": request.model,
        "messages": [m.model_dump(mode="json") for m in request.messages],
        "schema": (
            None
            if schema is None
            else {"name": schema.__name__, "fingerprint": schema_fingerprint(schema)}
        ),
        "temperature": float(request.temperature),
        "max_tokens": request.max_tokens,
        "tools": [t.model_dump(mode="json") for t in request.tools],
        "thinking": request.thinking,
        "prompt_version": request.metadata.get(PROMPT_VERSION_LABEL, ""),
    }


def cache_key(request: ProviderRequest, *, provider: str) -> str:
    """The cache key of `request` for `provider`: a 64-character SHA-256 hex digest."""
    payload = canonical_json(key_payload(request, provider=provider))
    return hashlib.sha256(payload.encode()).hexdigest()


def request_json(request: ProviderRequest, *, provider: str) -> str:
    """The canonical JSON of `key_payload`, as stored next to the answer (readable; its
    SHA-256 is the key)."""
    return canonical_json(key_payload(request, provider=provider))


class StoredResult(AtlasModel):
    """The answer of one gateway call as stored: `content`, `tool_calls`, `finish_reason`,
    the summed `usage` (tokens) and `cost_usd` (USD) of the provider responses that produced
    it, and how many there were (`attempts`: 2 after a repair retry)."""

    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str = "stop"
    usage: Usage
    cost_usd: Annotated[float, Field(ge=0)]
    attempts: Annotated[int, Field(ge=1)] = 1


@dataclass(frozen=True)
class CacheEntry:
    """One row of `llm_cache`: the `key`, when it was recorded (`created_at`, tz-aware UTC),
    the `provider` and `model` that answered, the canonical `request_json`, and the answer
    (`response_json`) and tokens and cost (`usage_json`) as JSON text."""

    key: str
    created_at: datetime
    provider: str
    model: str
    request_json: str
    response_json: str
    usage_json: str

    @classmethod
    def create(
        cls,
        *,
        key: str,
        request_json: str,
        provider: str,
        model: str,
        result: StoredResult,
        created_at: datetime,
    ) -> "CacheEntry":
        """An entry that stores `result` under `key`."""
        answer = result.model_dump(mode="json", include={"content", "tool_calls", "finish_reason"})
        meter = result.model_dump(mode="json", include={"usage", "cost_usd", "attempts"})
        return cls(
            key=key,
            created_at=created_at,
            provider=provider,
            model=model,
            request_json=request_json,
            response_json=canonical_json(answer),
            usage_json=canonical_json(meter),
        )

    def result(self) -> StoredResult:
        """The stored answer. Raises `LLMError` when the stored JSON is corrupt: a cache never
        guesses."""
        try:
            answer, meter = json.loads(self.response_json), json.loads(self.usage_json)
            return StoredResult.model_validate({**answer, **meter})
        except (ValueError, TypeError, ValidationError) as exc:
            raise LLMError(f"corrupt LLM cache entry {self.key}: {exc}") from exc


@dataclass(frozen=True)
class CacheStats:
    """What the store holds: `entries`, their size in `bytes` (UTF-8 bytes of the three JSON
    columns), `models` (entries per model id) and the `oldest` / `newest` `created_at`."""

    entries: int
    bytes: int
    models: Mapping[str, int]
    oldest: datetime | None
    newest: datetime | None


class LLMCache:
    """The store of recorded answers, keyed by `cache_key`.

    With a `database` the entries live in its native DuckDB table `llm_cache` (shared by every
    session on the lake); without one they live in memory, which is enough for unit tests and
    one-off scripts. Thread-safe.
    """

    def __init__(self, database: Database | None = None) -> None:
        self._database = database
        self._lock = threading.Lock()
        self._entries: dict[str, CacheEntry] = {}

    def get(self, key: str) -> CacheEntry | None:
        """The entry stored under `key`, or `None`."""
        if self._database is None:
            with self._lock:
                return self._entries.get(key)
        frame = self._database.query("SELECT * FROM llm_cache WHERE key = ?", [key])
        return _entry_from_row(frame.iloc[0].to_dict()) if len(frame) else None

    def put(self, entry: CacheEntry, *, replace: bool = False) -> bool:
        """Store `entry`. An existing entry with the same key is kept (the first writer wins)
        unless `replace`. Returns `True` when `entry` was stored."""
        if self._database is None:
            with self._lock:
                if entry.key in self._entries and not replace:
                    return False
                self._entries[entry.key] = entry
                return True
        row = {
            "key": entry.key,
            "created_at": entry.created_at,
            "provider": entry.provider,
            "model": entry.model,
            "request_json": entry.request_json,
            "response_json": entry.response_json,
            "usage": entry.usage_json,
        }
        return self._database.put_llm_cache([row], replace=replace) == 1

    def entries(self) -> tuple[CacheEntry, ...]:
        """Every entry, oldest first (ties by key)."""
        if self._database is None:
            with self._lock:
                found = list(self._entries.values())
            return tuple(sorted(found, key=lambda e: (e.created_at, e.key)))
        frame = self._database.query("SELECT * FROM llm_cache ORDER BY created_at, key")
        return tuple(_entry_from_row(r) for r in frame.to_dict(orient="records"))

    def stats(self) -> CacheStats:
        """Counts, size and age range of the stored entries (computed in DuckDB when the
        cache has a database, without reading the stored JSON into memory)."""
        if self._database is not None:
            return self._database_stats(self._database)
        found = self.entries()
        models: dict[str, int] = {}
        for e in found:
            models[e.model] = models.get(e.model, 0) + 1
        return CacheStats(
            entries=len(found),
            bytes=sum(
                len(e.request_json.encode())
                + len(e.response_json.encode())
                + len(e.usage_json.encode())
                for e in found
            ),
            models=FrozenDict(models),
            oldest=found[0].created_at if found else None,
            newest=max(e.created_at for e in found) if found else None,
        )

    @staticmethod
    def _database_stats(database: Database) -> CacheStats:
        totals = database.query(
            "SELECT count(*) AS n, coalesce(sum(octet_length(encode(request_json)) "
            "+ octet_length(encode(response_json)) + octet_length(encode(usage))), 0) AS size, "
            "min(created_at) AS oldest, max(created_at) AS newest FROM llm_cache"
        ).iloc[0]
        per_model = database.query(
            "SELECT model, count(*) AS n FROM llm_cache GROUP BY model ORDER BY model"
        )
        n = int(totals["n"])
        return CacheStats(
            entries=n,
            bytes=int(totals["size"]),
            models=FrozenDict(
                {str(m): int(c) for m, c in zip(per_model["model"], per_model["n"], strict=True)}
            ),
            oldest=totals["oldest"].to_pydatetime() if n else None,
            newest=totals["newest"].to_pydatetime() if n else None,
        )

    def prune(self, before: datetime) -> int:
        """Delete the entries created strictly before the tz-aware instant `before`; return
        how many were deleted."""
        if self._database is not None:
            return self._database.prune_llm_cache(before)
        if before.tzinfo is None or before.utcoffset() is None:
            raise ValueError(f"naive datetime {before!r}; pass a timezone-aware datetime")
        with self._lock:
            stale = [k for k, e in self._entries.items() if e.created_at < before]
            for key in stale:
                del self._entries[key]
        return len(stale)


def _entry_from_row(row: Mapping[Any, Any]) -> CacheEntry:
    created = row["created_at"]
    return CacheEntry(
        key=str(row["key"]),
        created_at=created.to_pydatetime() if hasattr(created, "to_pydatetime") else created,
        provider=str(row["provider"]),
        model=str(row["model"]),
        request_json=str(row["request_json"]),
        response_json=str(row["response_json"]),
        usage_json=str(row["usage"]),
    )


__all__ = [
    "KEY_VERSION",
    "PROMPT_VERSION_LABEL",
    "CacheEntry",
    "CacheMode",
    "CacheStats",
    "LLMCache",
    "StoredResult",
    "cache_key",
    "key_payload",
    "request_json",
    "schema_fingerprint",
]
