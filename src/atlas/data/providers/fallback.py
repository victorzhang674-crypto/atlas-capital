"""A provider chain: a primary vendor with a secondary one behind it, and the factory that
builds the chain the configuration asks for (design spec §16: vendor breakage)."""

from collections.abc import Sequence
from datetime import date

import pandas as pd

from atlas.core.config import AtlasConfig
from atlas.core.errors import ConfigError, DataError
from atlas.core.logging import get_logger
from atlas.data.providers.alpaca_provider import AlpacaPriceProvider
from atlas.data.providers.base import PriceProvider, check_request, empty_actions, empty_daily
from atlas.data.providers.yfinance_provider import YFinanceProvider

_log = get_logger(__name__)


class FallbackPriceProvider:
    """`primary` first, `secondary` for whatever the primary could not serve.

    `fetch_daily`: symbols the primary returned no rows for (all of them if it raised
    `DataError`) are asked of the secondary. Both raising `DataError` raises `DataError`; an
    empty answer is not a failure (a delisted symbol has no data), so the result may still
    lack symbols. `fetch_actions`: each symbol goes to the provider that served its prices in
    the last `fetch_daily` (the primary if none did), so actions match the prices they adjust;
    on `DataError` the other provider is asked; both failing raises `DataError`.
    """

    name = "fallback"

    def __init__(self, primary: PriceProvider, secondary: PriceProvider) -> None:
        """A chain of two providers."""
        self.primary = primary
        self.secondary = secondary
        self._served: dict[str, PriceProvider] = {}

    def fetch_daily(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        """Daily bars from the primary, gaps filled from the secondary."""
        wanted = check_request(symbols, start, end)
        if not wanted:
            return empty_daily()
        primary_error: DataError | None = None
        try:
            first = self.primary.fetch_daily(wanted, start, end)
        except DataError as exc:
            _log.warning("prices.primary_failed", vendor=self.primary.name, error=str(exc))
            primary_error, first = exc, empty_daily()
        got = set(first["symbol"])
        missing = [s for s in wanted if s not in got]
        second = empty_daily()
        if missing:
            try:
                second = self.secondary.fetch_daily(missing, start, end)
            except DataError as exc:
                if primary_error is not None:
                    raise DataError(
                        f"both price providers failed ({self.primary.name}: {primary_error}; "
                        f"{self.secondary.name}: {exc})"
                    ) from exc
                _log.warning("prices.secondary_failed", vendor=self.secondary.name, error=str(exc))
            if not second.empty:
                _log.info(
                    "prices.fallback_used",
                    vendor=self.secondary.name,
                    symbols=sorted(set(second["symbol"])),
                )
        for symbol in got:
            self._served[symbol] = self.primary
        for symbol in set(second["symbol"]):
            self._served[symbol] = self.secondary
        frames = [f for f in (first, second) if not f.empty]
        if not frames:
            return empty_daily()
        out = pd.concat(frames, ignore_index=True)
        return out.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)

    def fetch_actions(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        """Actions of each symbol from the provider that served its prices (see class doc)."""
        wanted = check_request(symbols, start, end)
        groups: dict[int, list[str]] = {}
        for symbol in wanted:
            source = self._served.get(symbol, self.primary)
            groups.setdefault(0 if source is self.primary else 1, []).append(symbol)
        frames = []
        for which, group in sorted(groups.items()):
            first, other = (
                (self.primary, self.secondary) if which == 0 else (self.secondary, self.primary)
            )
            try:
                frames.append(first.fetch_actions(group, start, end))
            except DataError as exc:
                _log.warning("prices.actions_failed", vendor=first.name, error=str(exc))
                try:
                    frames.append(other.fetch_actions(group, start, end))
                except DataError as exc2:
                    raise DataError(
                        f"both providers failed to fetch actions ({first.name}: {exc}; "
                        f"{other.name}: {exc2})"
                    ) from exc2
        frames = [f for f in frames if not f.empty]
        if not frames:
            return empty_actions()
        out = pd.concat(frames, ignore_index=True)
        return out.sort_values(["symbol", "ex_date", "type"], kind="stable").reset_index(drop=True)


def build_price_provider(cfg: AtlasConfig) -> PriceProvider:
    """The price provider chain for `cfg`.

    The primary is `cfg.data.providers.prices`; the other vendor backs it up when usable
    (Alpaca needs both `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY`). `prices: alpaca`
    without keys raises `ConfigError`. Without Alpaca keys the result is yfinance alone.
    """
    yahoo = YFinanceProvider(cfg.data.prices)
    alpaca = AlpacaPriceProvider.from_config(cfg)
    if cfg.data.providers.prices == "alpaca":
        if alpaca is None:
            raise ConfigError(
                "data.providers.prices is 'alpaca' but ALPACA_API_KEY_ID / "
                "ALPACA_API_SECRET_KEY are not set"
            )
        return FallbackPriceProvider(alpaca, yahoo)
    if alpaca is None:
        return yahoo
    return FallbackPriceProvider(yahoo, alpaca)
