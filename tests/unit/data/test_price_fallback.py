"""Stage 07 Task 4: the fallback provider and the configured provider chain."""

from datetime import date

import pandas as pd
import pytest
from price_helpers import FakePriceProvider, daily_rows
from pydantic import SecretStr

from atlas.core.config import AtlasConfig, DataConfig, DataProviders, Secrets
from atlas.core.errors import ConfigError, DataError
from atlas.data.providers.alpaca_provider import AlpacaPriceProvider
from atlas.data.providers.base import PriceProvider, ServedRecorder
from atlas.data.providers.fallback import FallbackPriceProvider, build_price_provider
from atlas.data.providers.yfinance_provider import YFinanceProvider

D1, D2 = date(2024, 3, 1), date(2024, 3, 4)


def _daily(vendor: str, *symbols: str) -> pd.DataFrame:
    frames = [daily_rows(s, [(D1, 10.0, 100), (D2, 11.0, 100)], vendor) for s in symbols]
    return pd.concat(frames, ignore_index=True)


def _actions(vendor: str, symbol: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": [symbol],
            "ex_date": [D2],
            "type": ["dividend"],
            "ratio": [float("nan")],
            "amount": [0.5],
            "vendor": [vendor],
        }
    )


def test_is_a_price_provider() -> None:
    chain = FallbackPriceProvider(FakePriceProvider(name="a"), FakePriceProvider(name="b"))
    assert isinstance(chain, PriceProvider)
    assert chain.name == "fallback"


def test_primary_empty_uses_secondary() -> None:
    primary = FakePriceProvider(name="a")
    secondary = FakePriceProvider(_daily("b", "AAPL"), name="b")
    out = FallbackPriceProvider(primary, secondary).fetch_daily(["AAPL"], D1, D2)
    assert set(out["vendor"]) == {"b"} and len(out) == 2


def test_primary_partial_fills_missing_from_secondary() -> None:
    primary = FakePriceProvider(_daily("a", "AAPL"), name="a")
    secondary = FakePriceProvider(_daily("b", "AAPL", "SPY"), name="b")
    out = FallbackPriceProvider(primary, secondary).fetch_daily(["AAPL", "SPY"], D1, D2)
    assert dict(zip(out["symbol"], out["vendor"], strict=True)) == {"AAPL": "a", "SPY": "b"}
    assert secondary.calls == [("daily", ("SPY",), D1, D2)]  # only the missing symbol


def test_primary_complete_never_calls_secondary() -> None:
    primary = FakePriceProvider(_daily("a", "AAPL"), name="a")
    secondary = FakePriceProvider(_daily("b", "AAPL"), name="b")
    FallbackPriceProvider(primary, secondary).fetch_daily(["AAPL"], D1, D2)
    assert secondary.calls == []


def test_primary_error_uses_secondary() -> None:
    primary = FakePriceProvider(_daily("a", "AAPL"), name="a", fail_all=True)
    secondary = FakePriceProvider(_daily("b", "AAPL"), name="b")
    out = FallbackPriceProvider(primary, secondary).fetch_daily(["AAPL"], D1, D2)
    assert set(out["vendor"]) == {"b"}


def test_both_fail_raises_data_error() -> None:
    primary = FakePriceProvider(name="a", fail_all=True)
    secondary = FakePriceProvider(name="b", fail_all=True)
    with pytest.raises(DataError, match="both") as info:
        FallbackPriceProvider(primary, secondary).fetch_daily(["AAPL"], D1, D2)
    assert isinstance(info.value.__cause__, DataError)


def test_secondary_error_after_partial_primary_keeps_primary_rows() -> None:
    primary = FakePriceProvider(_daily("a", "AAPL"), name="a")
    secondary = FakePriceProvider(name="b", fail_all=True)
    out = FallbackPriceProvider(primary, secondary).fetch_daily(["AAPL", "SPY"], D1, D2)
    assert set(out["symbol"]) == {"AAPL"}


def test_both_empty_is_not_an_error() -> None:
    chain = FallbackPriceProvider(FakePriceProvider(name="a"), FakePriceProvider(name="b"))
    assert chain.fetch_daily(["SIVB"], D1, D2).empty


def test_actions_follow_the_provider_that_served_prices() -> None:
    primary = FakePriceProvider(_daily("a", "AAPL"), _actions("a", "AAPL"), name="a")
    secondary = FakePriceProvider(
        _daily("b", "AAPL", "SPY"),
        pd.concat([_actions("b", "AAPL"), _actions("b", "SPY")]),
        name="b",
    )
    chain = FallbackPriceProvider(primary, secondary)
    chain.fetch_daily(["AAPL", "SPY"], D1, D2)
    out = chain.fetch_actions(["AAPL", "SPY"], D1, D2)
    assert dict(zip(out["symbol"], out["vendor"], strict=True)) == {"AAPL": "a", "SPY": "b"}


def test_record_served_routes_actions_by_vendor() -> None:  # audit item 9
    primary = FakePriceProvider(actions=_actions("a", "AAPL"), name="a")
    secondary = FakePriceProvider(
        actions=pd.concat([_actions("b", "AAPL"), _actions("b", "SPY")]), name="b"
    )
    chain = FallbackPriceProvider(primary, secondary)
    assert isinstance(chain, ServedRecorder)
    chain.record_served(pd.concat([_daily("b", "AAPL"), _daily("zzz", "SPY")]))
    out = chain.fetch_actions(["AAPL", "SPY"], D1, D2)
    # AAPL → b (recorded); SPY's vendor "zzz" is unknown → ignored → primary (has no SPY actions)
    assert dict(zip(out["symbol"], out["vendor"], strict=True)) == {"AAPL": "b"}


def test_actions_unseen_symbols_go_to_primary_then_backup_on_error() -> None:
    primary = FakePriceProvider(name="a", fail_actions=["AAPL"])
    secondary = FakePriceProvider(actions=_actions("b", "AAPL"), name="b")
    out = FallbackPriceProvider(primary, secondary).fetch_actions(["AAPL"], D1, D2)
    assert list(out["vendor"]) == ["b"]
    assert primary.calls[0][0] == "actions"


def test_actions_both_fail_raises_data_error() -> None:
    chain = FallbackPriceProvider(
        FakePriceProvider(name="a", fail_all=True), FakePriceProvider(name="b", fail_all=True)
    )
    with pytest.raises(DataError, match="both"):
        chain.fetch_actions(["AAPL"], D1, D2)


def _cfg(prices: str = "yfinance", keys: bool = False) -> AtlasConfig:
    secrets = (
        Secrets(alpaca_api_key_id=SecretStr("k"), alpaca_api_secret_key=SecretStr("s"))
        if keys
        else Secrets()
    )
    return AtlasConfig(
        data=DataConfig(providers=DataProviders(prices=prices)),  # type: ignore[arg-type]
        secrets=secrets,
    )


def test_factory_yfinance_only_without_keys() -> None:
    assert isinstance(build_price_provider(_cfg()), YFinanceProvider)


def test_factory_adds_alpaca_when_keys_set() -> None:
    chain = build_price_provider(_cfg(keys=True))
    assert isinstance(chain, FallbackPriceProvider)
    assert isinstance(chain.primary, YFinanceProvider)
    assert isinstance(chain.secondary, AlpacaPriceProvider)


def test_factory_alpaca_primary_with_keys() -> None:
    chain = build_price_provider(_cfg("alpaca", keys=True))
    assert isinstance(chain, FallbackPriceProvider)
    assert isinstance(chain.primary, AlpacaPriceProvider)
    assert isinstance(chain.secondary, YFinanceProvider)


def test_factory_alpaca_primary_without_keys_is_config_error() -> None:
    with pytest.raises(ConfigError, match="ALPACA"):
        build_price_provider(_cfg("alpaca"))
