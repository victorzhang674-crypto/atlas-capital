"""FRED / ALFRED API client: series metadata, vintage dates, observations by real-time period.

FRED allows 120 requests per minute; the client throttles every request to
`max_requests_per_second`, retries transport errors, HTTP 429 and 5xx answers
(`call_with_retry`, Stage 07), reports FRED's own message on HTTP 400 and fails at once on any
other non-200 answer. Long answers are paged with `limit`/`offset`.

The API key (`FRED_API_KEY`) travels as a query parameter, so it is part of every request URL:
log events, error messages and cache file names are built without it, any text that came from
the network is redacted before it is logged or raised, and the `httpx`/`httpcore` loggers
(which log every request URL at INFO/DEBUG) get a filter that masks `api_key=` values.

With a lake, every JSON answer is archived under `raw/fred/<endpoint>/` as received (written
atomically, only after it parsed as a JSON object). A cached file is served instead of a
request unless `refresh=True`.
"""

import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO, Self

import httpx

from atlas.core.config import AtlasConfig, MacroConfig
from atlas.core.errors import ConfigError, DataError
from atlas.core.logging import get_logger
from atlas.core.storage import Lake
from atlas.core.storage.meta import atomic_write
from atlas.data.providers.base import RetryPolicy, Throttle, call_with_retry

_log = get_logger(__name__)

RAW_VENDOR = "fred"
"""Vendor directory of the raw archive: `raw/fred/`."""

VINTAGE_PAGE_MAX = 10_000
"""FRED's largest page of `series/vintagedates` (`limit` ≤ 10000)."""


_API_KEY_RE = re.compile(r"(api_key=)[^&\s\"']+")


class _RedactApiKey(logging.Filter):
    """Masks `api_key=<value>` in a log record's message (httpx logs every request URL)."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite the record's message with the key masked; never drop the record."""
        message = record.getMessage()
        redacted = _API_KEY_RE.sub(r"\1***", message)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True


_REDACT = _RedactApiKey()
for _name in ("httpx", "httpcore"):
    if not any(isinstance(f, _RedactApiKey) for f in logging.getLogger(_name).filters):
        logging.getLogger(_name).addFilter(_REDACT)


class _Retryable(Exception):
    """A failure worth retrying: a transport error or an HTTP 429 / 5xx answer."""


class FredClient:
    """Fetches FRED JSON with an API key, a rate limit, retries, paging and a raw cache.

    `lake` (optional) is where answers are archived (`raw/fred/`); `http` and `sleep` are
    injectable for tests. A blank `api_key` raises `ConfigError`. Dates are passed to FRED as
    ISO strings; real-time dates are vintage (knowledge) dates, observation dates are the
    periods the values describe.
    """

    def __init__(
        self,
        config: MacroConfig,
        *,
        api_key: str,
        lake: Lake | None = None,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key.strip():
            raise ConfigError("the FRED API needs an API key (FRED_API_KEY)")
        self._config = config
        self._key = api_key
        self._lake = lake
        self._sleep = sleep
        self._policy = RetryPolicy(
            max_retries=config.max_retries,
            backoff_seconds=config.backoff_seconds,
            min_interval_seconds=1.0 / config.max_requests_per_second,
        )
        self._throttle = Throttle(self._policy.min_interval_seconds, sleep)
        self._owns_http = http is None
        self._http = (
            http if http is not None else httpx.Client(timeout=config.request_timeout_seconds)
        )

    # --- context management --------------------------------------------------------------------

    def close(self) -> None:
        """Close the HTTP connection pool (only if this client created it)."""
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- endpoints -------------------------------------------------------------------------------

    def series(self, series_id: str, *, refresh: bool = False) -> dict[str, Any]:
        """`fred/series`: the series' metadata (`{"seriess": [{id, title, frequency_short,
        units, seasonal_adjustment_short, ...}]}`)."""
        return self._get("series", {"series_id": series_id}, f"{series_id}.json", refresh)

    def vintage_dates(
        self, series_id: str, *, realtime_start: date, realtime_end: date, refresh: bool = False
    ) -> tuple[date, ...]:
        """`fred/series/vintagedates`: every vintage date of `series_id` in
        `[realtime_start, realtime_end]`, ascending (pages of `min(page_limit, 10000)`)."""
        limit = min(self._config.page_limit, VINTAGE_PAGE_MAX)
        dates: list[date] = []
        offset = 0
        while True:
            params = {
                "series_id": series_id,
                "realtime_start": realtime_start.isoformat(),
                "realtime_end": realtime_end.isoformat(),
                "limit": str(limit),
                "offset": str(offset),
            }
            name = f"{series_id}_{realtime_start}_{realtime_end}_{offset}.json"
            page = self._get("series/vintagedates", params, name, refresh)
            items = page.get("vintage_dates")
            count = page.get("count")
            if not isinstance(items, list) or not isinstance(count, int):
                raise DataError(f"FRED vintagedates {series_id}: no vintage_dates/count")
            try:
                dates.extend(date.fromisoformat(d) for d in items)
            except (TypeError, ValueError) as exc:
                raise DataError(f"FRED vintagedates {series_id}: bad date ({exc})") from exc
            offset += len(items)
            if offset >= count or not items:
                break
        if len(dates) != count:
            raise DataError(f"FRED vintagedates {series_id}: got {len(dates)} of {count} dates")
        return tuple(dates)

    def observations(
        self,
        series_id: str,
        *,
        observation_start: date,
        observation_end: date,
        realtime_start: date,
        realtime_end: date,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """`fred/series/observations` with `output_type=1` (observations by real-time period):
        rows `{realtime_start, realtime_end, date, value}` (value a string, `"."` = missing).

        Pages of `page_limit` rows are fetched until `count` rows arrived and returned as one
        payload; fewer rows than `count` raise `DataError`. The real-time window may cover at
        most 2000 vintage dates (FRED's limit; see `vintage_windows`).
        """
        dates = (observation_start, observation_end, realtime_start, realtime_end)
        base = {
            "series_id": series_id,
            "observation_start": observation_start.isoformat(),
            "observation_end": observation_end.isoformat(),
            "realtime_start": realtime_start.isoformat(),
            "realtime_end": realtime_end.isoformat(),
            "output_type": "1",
            "limit": str(self._config.page_limit),
        }
        stem = "_".join([series_id, *(d.isoformat() for d in dates)])
        rows: list[Any] = []
        first: dict[str, Any] | None = None
        while True:
            offset = len(rows)
            page = self._get(
                "series/observations",
                {**base, "offset": str(offset)},
                f"{stem}_{offset}.json",
                refresh,
            )
            items = page.get("observations")
            count = page.get("count")
            if not isinstance(items, list) or not isinstance(count, int):
                raise DataError(f"FRED observations {series_id}: no observations/count")
            first = page if first is None else first
            rows.extend(items)
            if len(rows) >= count or not items:
                break
        if len(rows) != count:
            raise DataError(f"FRED observations {series_id}: got {len(rows)} of {count} rows")
        return {**first, "offset": 0, "observations": rows}

    # --- plumbing --------------------------------------------------------------------------------

    def _redact(self, text: str) -> str:
        return text.replace(self._key, "***")

    def _cache(self, endpoint: str, name: str) -> Path | None:
        if self._lake is None:
            return None
        return self._lake.raw_path(RAW_VENDOR, endpoint.rsplit("/", 1)[-1]) / name

    def _get(
        self, endpoint: str, params: Mapping[str, str], name: str, refresh: bool
    ) -> dict[str, Any]:
        cache = self._cache(endpoint, name)
        if cache is not None and not refresh and cache.exists():
            return _decode(cache.read_bytes(), str(cache))
        shown = " ".join(f"{k}={v}" for k, v in params.items())
        what = f"FRED {endpoint} {shown}"
        body = call_with_retry(
            lambda: self._request(endpoint, params, what),
            self._policy,
            self._sleep,
            what,
            (_Retryable,),
            event="macro.request_failed",
        )
        data = _decode(body, what)
        if cache is not None:

            def _write(fh: BinaryIO) -> None:
                fh.write(body)

            atomic_write(cache, _write)
        return data

    def _request(self, endpoint: str, params: Mapping[str, str], what: str) -> bytes:
        self._throttle.wait()
        query = {**params, "api_key": self._key, "file_type": "json"}
        failure: str | None = None
        try:
            resp = self._http.get(f"{self._config.api_url}/{endpoint}", params=query)
        except httpx.TransportError as exc:
            failure = self._redact(f"{type(exc).__name__}: {exc}")
        if failure is not None:
            # Raised outside the `except` block so the original error (whose text may carry
            # the request URL, and so the key) is neither the cause nor the context.
            raise _Retryable(failure)
        if resp.status_code == 200:
            return resp.content
        if resp.status_code == 429 or resp.status_code >= 500:
            raise _Retryable(f"HTTP {resp.status_code}")
        message = ""
        if resp.status_code == 400:
            try:
                message = str(json.loads(resp.content).get("error_message", ""))
            except (ValueError, AttributeError):
                message = ""
        raise DataError(self._redact(f"{what} returned HTTP {resp.status_code} {message}".strip()))


def _decode(body: bytes, where: str) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise DataError(f"{where}: not valid JSON ({exc})") from None
    if not isinstance(data, dict):
        raise DataError(f"{where}: expected a JSON object, got {type(data).__name__}")
    return data


def build_fred_client(config: AtlasConfig, lake: Lake | None = None) -> FredClient:
    """A `FredClient` from the run's config: `config.data.macro` and the `FRED_API_KEY` secret
    (`ConfigError` naming it when unset). Answers are archived in `lake`'s `raw/fred/` when a
    lake is given."""
    secret = config.secrets.fred_api_key
    key = secret.get_secret_value() if secret is not None else ""
    if not key.strip():
        raise ConfigError(
            "FRED_API_KEY is not set: the FRED API needs a free key "
            "(https://fred.stlouisfed.org/docs/api/api_key.html); add it to .env"
        )
    return FredClient(config.data.macro, api_key=key, lake=lake)
