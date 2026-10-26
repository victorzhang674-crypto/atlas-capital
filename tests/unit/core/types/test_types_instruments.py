"""Tests for symbol normalisation and `Instrument`."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from atlas.core.types.base import AtlasModel, FrozenDict
from atlas.core.types.instruments import (
    AssetClass,
    Instrument,
    SymbolWeights,
    normalize_symbol,
    vendor_symbol,
)


class _Book(AtlasModel):
    weights: SymbolWeights


@pytest.mark.parametrize("raw", ["BRK.B", "BRK-B", "BRK/B", "brk.b", " brk-b \n", "Brk.b"])
def test_normalize_class_shares(raw: str) -> None:
    assert normalize_symbol(raw) == "BRK.B"


@pytest.mark.parametrize("raw", ["aapl", " AAPL ", "AAPL"])
def test_normalize_plain_ticker(raw: str) -> None:
    assert normalize_symbol(raw) == "AAPL"


@pytest.mark.parametrize(
    "raw", ["", "   ", "BR K", "BRK..B", "-B", "BRK.", "BRK.B.C", "AAPL$", "1ABC", "ABCDEFGHIJK"]
)
def test_normalize_rejects(raw: str) -> None:
    with pytest.raises(ValueError, match="symbol"):
        normalize_symbol(raw)


def test_vendor_symbol() -> None:
    assert vendor_symbol("BRK.B") == "BRK-B"
    assert vendor_symbol("BRK.B", "/") == "BRK/B"
    assert vendor_symbol("AAPL") == "AAPL"
    assert vendor_symbol("brk-b") == "BRK-B"


_canonical = st.from_regex(r"\A[A-Z][A-Z0-9]{0,9}(\.[A-Z0-9]{1,4})?\Z", fullmatch=True)


@settings(derandomize=True, max_examples=200)
@given(_canonical)
def test_normalize_properties(symbol: str) -> None:
    assert normalize_symbol(symbol) == symbol
    assert normalize_symbol(normalize_symbol(symbol.lower())) == symbol
    assert normalize_symbol(vendor_symbol(symbol)) == symbol


def test_symbol_map_normalises_keys_and_is_frozen() -> None:
    book = _Book(weights={"brk-b": 0.01, " aapl": -0.02})
    assert book.weights == {"BRK.B": 0.01, "AAPL": -0.02}
    assert isinstance(book.weights, FrozenDict)
    assert book.model_dump() == {"weights": {"BRK.B": 0.01, "AAPL": -0.02}}


def test_symbol_map_rejects_colliding_keys() -> None:
    with pytest.raises(ValidationError, match=r"BRK\.B"):
        _Book(weights={"BRK-B": 0.01, "BRK.B": 0.02})


def test_symbol_map_rejects_bad_key() -> None:
    with pytest.raises(ValidationError):
        _Book(weights={"BR K": 0.01})


def test_instrument_normalises_symbol() -> None:
    inst = Instrument(symbol="brk-b", asset_class=AssetClass.EQUITY, sector="Financials")
    assert inst.symbol == "BRK.B"
    assert inst.currency == "USD"
    assert inst.is_shortable is True
    assert inst.exchange is None


def test_asset_classes() -> None:
    assert [a.value for a in AssetClass] == ["EQUITY", "ETF", "CASH"]


def test_cash_cannot_be_shortable() -> None:
    with pytest.raises(ValidationError, match="shortable"):
        Instrument(symbol="USD", asset_class=AssetClass.CASH)
    cash = Instrument(symbol="USD", asset_class=AssetClass.CASH, is_shortable=False)
    assert cash.asset_class is AssetClass.CASH


@pytest.mark.parametrize("currency", ["usd", "US", "USDX", ""])
def test_currency_must_be_iso(currency: str) -> None:
    with pytest.raises(ValidationError):
        Instrument(symbol="SAP", asset_class=AssetClass.EQUITY, currency=currency)
