from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from nautilus_trader.model import Currency, CurrencyType

from crypto_max.mapping import (
    MappingError,
    currency_from_bitpin,
    decimal_places,
    instrument_from_bitpin,
)

PUBLIC_FIXTURE = Path(__file__).parent / "fixtures" / "bitpin" / "public.json"


def test_decimal_places_preserves_tick_precision() -> None:
    assert decimal_places(Decimal(1)) == 0
    assert decimal_places(Decimal("0.01")) == 2
    assert decimal_places(Decimal("0.00000001")) == 8


def test_currency_from_bitpin_registers_irt_as_fiat() -> None:
    currency = currency_from_bitpin(
        {
            "code": "IRT",
            "name": "Toman",
            "precision": Decimal(1),
        }
    )

    assert currency.code == "IRT"
    assert currency.precision == 0
    assert currency.currency_type == CurrencyType.FIAT
    assert Currency.from_str("IRT") == currency


def test_instrument_from_bitpin_preserves_native_identity_and_increments() -> None:
    currency_from_bitpin({"code": "IRT", "name": "Toman", "precision": Decimal(1)})
    currency_from_bitpin(
        {"code": "USDT", "name": "Tether", "precision": Decimal("0.01")}
    )
    instrument = instrument_from_bitpin(
        {
            "id": "USDT_IRT",
            "symbol": "USDT/IRT",
            "base": "USDT",
            "quote": "IRT",
            "baseId": "USDT",
            "quoteId": "IRT",
            "type": "spot",
            "spot": True,
            "precision": {"price": Decimal(1), "amount": Decimal("0.01")},
            "limits": {
                "amount": {"min": None, "max": None},
                "price": {"min": None, "max": None},
                "cost": {"min": None, "max": None},
            },
            "info": {"symbol": "USDT_IRT"},
        },
        ts_event=123,
        ts_init=456,
    )

    assert str(instrument.id) == "USDT_IRT.BITPIN"
    assert str(instrument.raw_symbol) == "USDT_IRT"
    assert instrument.price_precision == 0
    assert instrument.size_precision == 2
    assert instrument.price_increment == 1
    assert instrument.size_increment == Decimal("0.01")
    assert instrument.info == {"symbol": "USDT_IRT"}
    assert instrument.ts_event == 123
    assert instrument.ts_init == 456


def test_instrument_rejects_non_spot_market() -> None:
    with pytest.raises(MappingError, match="only BitPin spot"):
        instrument_from_bitpin(
            {
                "id": "BTC_PERP",
                "symbol": "BTC/PERP",
                "base": "BTC",
                "quote": "PERP",
                "type": "swap",
                "spot": False,
            }
        )


def test_captured_public_fixture_maps_the_observed_spot_markets() -> None:
    fixture = json.loads(PUBLIC_FIXTURE.read_text(encoding="utf-8"))
    for record in fixture["currencies"].values():
        currency_from_bitpin(record)

    instruments = {
        symbol: instrument_from_bitpin(record)
        for symbol, record in fixture["markets"].items()
    }

    assert set(instruments) == {"USDT/IRT", "BTC/IRT", "BTC/USDT"}
    assert {str(instrument.id) for instrument in instruments.values()} == {
        "USDT_IRT.BITPIN",
        "BTC_IRT.BITPIN",
        "BTC_USDT.BITPIN",
    }
    assert all(fixture["trades"][symbol] for symbol in instruments)
    assert all(fixture["candles"][symbol] for symbol in instruments)
