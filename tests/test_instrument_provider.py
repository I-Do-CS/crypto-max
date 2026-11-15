from __future__ import annotations

import asyncio
import json
from pathlib import Path

from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.model import CurrencyType, InstrumentId

from crypto_max.instrument_provider import BitpinInstrumentProvider

PUBLIC_FIXTURE = Path(__file__).parent / "fixtures" / "bitpin" / "public.json"


def _fixture_records() -> tuple[list[dict], list[dict]]:
    fixture = json.loads(PUBLIC_FIXTURE.read_text(encoding="utf-8"))
    return list(fixture["markets"].values()), list(fixture["currencies"].values())


def test_provider_loads_fixture_markets_and_currencies() -> None:
    markets, currencies = _fixture_records()
    provider = BitpinInstrumentProvider(
        market_records=markets,
        currency_records=currencies,
    )

    asyncio.run(provider.load_all_async())

    assert {str(instrument.id) for instrument in provider.list_all()} == {
        "USDT_IRT.BITPIN",
        "BTC_IRT.BITPIN",
        "BTC_USDT.BITPIN",
    }
    assert provider.currency("IRT").currency_type == CurrencyType.FIAT
    assert provider.currency("IRT").precision == 0


def test_provider_filters_exact_symbols() -> None:
    markets, currencies = _fixture_records()
    provider = BitpinInstrumentProvider(
        InstrumentProviderConfig(filters={"symbols": ["BTC/USDT"]}),
        market_records=markets,
        currency_records=currencies,
    )

    asyncio.run(provider.load_all_async())

    assert [str(instrument.id) for instrument in provider.list_all()] == [
        "BTC_USDT.BITPIN"
    ]


def test_provider_resolves_approved_product_alias() -> None:
    markets, currencies = _fixture_records()
    provider = BitpinInstrumentProvider(
        InstrumentProviderConfig(filters={"symbols": ["USDT/BTC"]}),
        market_records=markets,
        currency_records=currencies,
    )

    asyncio.run(provider.load_all_async())

    assert [str(instrument.id) for instrument in provider.list_all()] == [
        "BTC_USDT.BITPIN"
    ]


def test_provider_load_ids_only_registers_requested_market() -> None:
    markets, currencies = _fixture_records()
    provider = BitpinInstrumentProvider(
        market_records=markets,
        currency_records=currencies,
    )
    requested = InstrumentId.from_str("BTC_IRT.BITPIN")

    asyncio.run(provider.load_ids_async([requested]))

    assert [str(instrument.id) for instrument in provider.list_all()] == [
        "BTC_IRT.BITPIN"
    ]
    assert provider.find(InstrumentId.from_str("USDT_IRT.BITPIN")) is None


def test_provider_accepts_async_mapping_currency_loader() -> None:
    markets, currencies = _fixture_records()

    async def load_markets() -> list[dict]:
        return markets

    async def load_currencies() -> dict[str, dict]:
        return {record["code"]: record for record in currencies}

    provider = BitpinInstrumentProvider(
        market_loader=load_markets,
        currency_loader=load_currencies,
    )

    asyncio.run(provider.load_all_async({"symbols": ["BTC/USDT"]}))

    assert [str(instrument.id) for instrument in provider.list_all()] == [
        "BTC_USDT.BITPIN"
    ]
    assert provider.currency("USDT").precision == 2
