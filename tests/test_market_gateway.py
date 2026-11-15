from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from bitpin_sdk import Config
from nautilus_trader.model import InstrumentId

from crypto_max.instrument_provider import BitpinInstrumentProvider
from crypto_max.market_gateway import BitpinMarketDataGateway, GatewayStateError

PUBLIC_FIXTURE = Path(__file__).parent / "fixtures" / "bitpin" / "public.json"
INSTRUMENT_ID = InstrumentId.from_str("BTC_IRT.BITPIN")


class FakeAsyncBitpin:
    def __init__(self) -> None:
        fixture = json.loads(PUBLIC_FIXTURE.read_text(encoding="utf-8"))
        self.markets = list(fixture["markets"].values())
        self.currencies = fixture["currencies"]
        self.order_book = fixture["order_books"]["BTC/IRT"]
        self.trades = fixture["trades"]["BTC/IRT"]
        self.candles = fixture["candles"]["BTC/IRT"]

    async def fetch_markets(self) -> list[dict[str, Any]]:
        return self.markets

    async def fetch_currencies(self) -> dict[str, dict[str, Any]]:
        return self.currencies

    async def fetch_order_book(
        self, symbol: str, limit: int | None = None
    ) -> dict[str, Any]:
        assert symbol == "BTC/IRT"
        return self.order_book

    async def fetch_trades(
        self,
        symbol: str,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        assert symbol == "BTC/IRT"
        if since is not None:
            assert since == 1_000
        return self.trades[:limit] if limit is not None else self.trades

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[list[Any]]:
        assert symbol == "BTC/IRT"
        assert timeframe == "1m"
        if since is not None:
            assert since == 1_000
        return self.candles[:limit] if limit is not None else self.candles

    async def watch_order_book(
        self, symbol: str, timeout: float | None = None
    ) -> dict[str, Any]:
        return await self.fetch_order_book(symbol)

    async def watch_trades(
        self, symbol: str, timeout: float | None = None
    ) -> list[dict[str, Any]]:
        return await self.fetch_trades(symbol, limit=1)

    async def watch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        timeout: float | None = None,
    ) -> list[list[Any]]:
        return await self.fetch_ohlcv(symbol, timeframe, limit=1)

    async def close(self) -> None:
        pass


def _gateway() -> BitpinMarketDataGateway:
    fixture = json.loads(PUBLIC_FIXTURE.read_text(encoding="utf-8"))
    provider = BitpinInstrumentProvider(
        market_records=list(fixture["markets"].values()),
        currency_records=list(fixture["currencies"].values()),
    )
    return BitpinMarketDataGateway(
        Config(),
        client=FakeAsyncBitpin(),
        instrument_provider=provider,
    )


def test_gateway_lifecycle_and_typed_rest_data() -> None:
    gateway = _gateway()

    with pytest.raises(GatewayStateError):
        asyncio.run(gateway.fetch_quote(INSTRUMENT_ID))

    async def run() -> None:
        await gateway.connect()
        quote = await gateway.fetch_quote(INSTRUMENT_ID)
        trades = await gateway.fetch_trades(INSTRUMENT_ID, since=1_000, limit=2)
        bars = await gateway.fetch_bars(INSTRUMENT_ID, "1m", since=1_000, limit=2)
        assert str(quote.instrument_id) == str(INSTRUMENT_ID)
        assert len(trades) == 2
        assert len(bars) == 2
        await gateway.close()

    asyncio.run(run())


def test_gateway_maps_websocket_data_and_is_idempotent_on_close() -> None:
    gateway = _gateway()

    async def run() -> None:
        await gateway.connect()
        depth = await gateway.watch_order_book(INSTRUMENT_ID)
        trades = await gateway.watch_trades(INSTRUMENT_ID)
        bars = await gateway.watch_bars(INSTRUMENT_ID, "1m")
        assert depth.sequence == 0
        assert len(trades) == 1
        assert len(bars) == 1
        await gateway.close()
        await gateway.close()

    asyncio.run(run())
