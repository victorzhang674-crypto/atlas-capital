"""Alpaca market data adapter (data API v2; IEX feed on the free tier). Read-only market data:
no trading endpoint is touched here.

Daily bars come from `GET /v2/stocks/bars` twice per batch: `adjustment=raw` gives the
contract's raw OHLCV and `adjustment=all` gives `adj_close` (its close). Corporate actions come
from `GET /v1/corporate-actions` (forward and reverse splits, cash dividends; other kinds such
as stock dividends and spin-offs are not mapped). Both endpoints are paged with
`next_page_token`. Transport errors, HTTP 429 and 5xx are retried (see `PriceDataConfig`);
any other HTTP error raises `DataError` at once. The API keys are sent only as request
headers and never appear in logs, errors or `repr`.
"""

import time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import Any, Self

import httpx
import pandas as pd
from pydantic import SecretStr

from atlas.core.calendar import MARKET_TZ, market_date
from atlas.core.config import AtlasConfig, PriceDataConfig
from atlas.core.errors import DataError
from atlas.data.providers.base import (
    RetryPolicy,
    Throttle,
    call_with_retry,
    check_request,
    empty_actions,
    empty_daily,
    normalize_actions,
    normalize_daily,
)

_BARS_PATH = "/v2/stocks/bars"
_ACTIONS_PATH = "/v1/corporate-actions"
_BAR_PAGE_LIMIT = 10_000  # the API's maximum page size for bars
_ACTION_PAGE_LIMIT = 1_000  # the API's maximum page size for corporate actions
_ACTION_TYPES = "forward_split,reverse_split,cash_dividend"
# The corporate-actions date filter is on the processing date, which for a dividend is the
# payable date (weeks after the ex-date). Query this much further, then keep ex-dates in range.
_ACTION_QUERY_LOOKAHEAD = timedelta(days=90)


class _Retryable(Exception):
    """An HTTP answer worth retrying (429 or 5xx)."""


class AlpacaPriceProvider:
    """`PriceProvider` backed by Alpaca market data. `client`/`sleep` are injectable for tests."""

    name = "alpaca"

    def __init__(
        self,
        key_id: SecretStr,
        secret_key: SecretStr,
        config: PriceDataConfig | None = None,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """A provider using the given paper-account data keys and `config` (`cfg.data.prices`)."""
        cfg = config if config is not None else PriceDataConfig()
        self._key_id = key_id
        self._secret_key = secret_key
        self._base_url = cfg.alpaca_data_url.rstrip("/")
        self._feed = cfg.alpaca_feed
        self._batch_size = cfg.batch_size
        self._policy = RetryPolicy.from_config(cfg)
        self._sleep = sleep
        self._throttle = Throttle(self._policy.min_interval_seconds, sleep)
        self._owns_client = client is None
        self._client = (
            client if client is not None else httpx.Client(timeout=cfg.request_timeout_seconds)
        )

    @classmethod
    def from_config(cls, cfg: AtlasConfig) -> Self | None:
        """A provider from `cfg.secrets` and `cfg.data.prices`, or `None` if either Alpaca key
        is unset (Alpaca is optional)."""
        key_id, secret = cfg.secrets.alpaca_api_key_id, cfg.secrets.alpaca_api_secret_key
        if key_id is None or secret is None:
            return None
        if not key_id.get_secret_value() or not secret.get_secret_value():
            return None
        return cls(key_id, secret, cfg.data.prices)

    def __repr__(self) -> str:
        return f"AlpacaPriceProvider(base_url={self._base_url!r}, feed={self._feed!r})"

    def close(self) -> None:
        """Close the HTTP client if this provider created it."""
        if self._owns_client:
            self._client.close()

    # --- PriceProvider ------------------------------------------------------------------------

    def fetch_daily(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        """Raw daily bars plus the `adjustment=all` close of `symbols` in `[start, end]`."""
        wanted = check_request(symbols, start, end)
        frames = []
        for i in range(0, len(wanted), self._batch_size):
            batch = wanted[i : i + self._batch_size]
            raw = self._bars(batch, start, end, "raw")
            adjusted = self._bars(batch, start, end, "all")[["symbol", "date", "close"]]
            merged = raw.merge(
                adjusted.rename(columns={"close": "adj_close"}), on=["symbol", "date"], how="inner"
            )
            frames.append(merged)
        if not frames or all(f.empty for f in frames):
            return empty_daily()
        out = pd.concat([f for f in frames if not f.empty], ignore_index=True)
        out["vendor"] = self.name
        return normalize_daily(out)

    def fetch_actions(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        """Splits and cash dividends of `symbols` with ex-dates in `[start, end]`."""
        wanted = check_request(symbols, start, end)
        rows: list[dict[str, object]] = []
        for i in range(0, len(wanted), self._batch_size):
            batch = wanted[i : i + self._batch_size]
            params = {
                "symbols": ",".join(batch),
                "types": _ACTION_TYPES,
                "start": start.isoformat(),
                "end": (end + _ACTION_QUERY_LOOKAHEAD).isoformat(),
                "limit": str(_ACTION_PAGE_LIMIT),
                "sort": "asc",
            }
            for page in self._pages(_ACTIONS_PATH, params):
                rows.extend(_action_rows(page.get("corporate_actions") or {}, set(batch)))
        if not rows:
            return empty_actions()
        out = normalize_actions(pd.DataFrame(rows))
        keep = (out["ex_date"] >= start) & (out["ex_date"] <= end)
        return out[keep].reset_index(drop=True)

    # --- HTTP ---------------------------------------------------------------------------------

    def _bars(self, batch: Sequence[str], start: date, end: date, adjustment: str) -> pd.DataFrame:
        params = {
            "symbols": ",".join(batch),
            "timeframe": "1Day",
            "start": _instant(start, dtime(0, 0, 0)),
            "end": _instant(end, dtime(23, 59, 59)),
            "adjustment": adjustment,
            "feed": self._feed,
            "limit": str(_BAR_PAGE_LIMIT),
            "sort": "asc",
        }
        rows = []
        for page in self._pages(_BARS_PATH, params):
            for symbol, bars in (page.get("bars") or {}).items():
                for bar in bars:
                    day = market_date(_parse_ts(bar["t"]))
                    if start <= day <= end:
                        rows.append(
                            {
                                "symbol": symbol,
                                "date": day,
                                "open": bar["o"],
                                "high": bar["h"],
                                "low": bar["l"],
                                "close": bar["c"],
                                "volume": bar["v"],
                            }
                        )
        columns = ["symbol", "date", "open", "high", "low", "close", "volume"]
        return pd.DataFrame(rows, columns=columns)

    def _pages(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        """Every page of `path` for `params`, following `next_page_token`."""
        pages = []
        token: str | None = None
        while True:
            query = dict(params) if token is None else {**params, "page_token": token}
            page = self._get(path, query)
            pages.append(page)
            token = page.get("next_page_token")
            if not token:
                return pages

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = self._base_url + path
        self._throttle.wait()

        def _attempt() -> dict[str, Any]:
            response = self._client.get(url, params=params, headers=self._headers())
            if response.status_code == 429 or response.status_code >= 500:
                raise _Retryable(f"HTTP {response.status_code} from {path}")
            if response.status_code >= 400:
                raise DataError(
                    f"alpaca {path}: HTTP {response.status_code}: {response.text[:200]}"
                )
            try:
                body = response.json()
            except ValueError as exc:
                raise DataError(f"alpaca {path}: response is not JSON") from exc
            if not isinstance(body, dict):
                raise DataError(f"alpaca {path}: expected a JSON object")
            return body

        return call_with_retry(
            _attempt,
            self._policy,
            self._sleep,
            f"alpaca {path}",
            (_Retryable, httpx.TransportError),
        )

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._key_id.get_secret_value(),
            "APCA-API-SECRET-KEY": self._secret_key.get_secret_value(),
        }


def _instant(day: date, at: dtime) -> str:
    """`day` at `at` New York time, as an RFC 3339 UTC string (`...Z`)."""
    utc = datetime.combine(day, at, tzinfo=MARKET_TZ).astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _action_rows(actions: dict[str, Any], batch: set[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for kind in ("forward_splits", "reverse_splits"):
        for item in actions.get(kind) or []:
            if item["symbol"] in batch:
                rows.append(
                    {
                        "symbol": item["symbol"],
                        "ex_date": date.fromisoformat(item["ex_date"]),
                        "type": "split",
                        "ratio": float(item["new_rate"]) / float(item["old_rate"]),
                        "amount": float("nan"),
                        "vendor": AlpacaPriceProvider.name,
                    }
                )
    for item in actions.get("cash_dividends") or []:
        if item["symbol"] in batch:
            rows.append(
                {
                    "symbol": item["symbol"],
                    "ex_date": date.fromisoformat(item["ex_date"]),
                    "type": "dividend",
                    "ratio": float("nan"),
                    "amount": float(item["rate"]),
                    "vendor": AlpacaPriceProvider.name,
                }
            )
    return rows
