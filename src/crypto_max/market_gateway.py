"""Async BitPin market-data boundary used by the Nautilus data client."""

from __future__ import annotations

from bitpin_sdk import AsyncBitpin, Config
from nautilus_trader.model import (
    Bar,
    InstrumentId,
    OrderBookDepth,
    QuoteTick,
    TradeTick,
)

from .instrument_provider import BitpinInstrumentProvider
from .market_data import (
    bar_from_bitpin,
    order_book_depth_from_bitpin,
    quote_tick_from_order_book,
    trade_tick_from_bitpin,
)


class GatewayStateError(RuntimeError):
    """Raised when a gateway operation is attempted outside its lifecycle."""


class BitpinMarketDataGateway:
    """Translate AsyncBitpin market data into Nautilus objects.

    The gateway creates no client or task in its constructor. Call ``connect`` from the
    owning adapter's startup hook and ``close`` from its shutdown hook.
    """

    def __init__(
        self,
        config: Config,
        *,
        client: AsyncBitpin | None = None,
        instrument_provider: BitpinInstrumentProvider | None = None,
    ) -> None:
        self.config = config
        self._client = client
        self._owns_client = client is None
        self.instrument_provider = instrument_provider
        self._connected = False

    @property
    def client(self) -> AsyncBitpin:
        if self._client is None or not self._connected:
            raise GatewayStateError("BitPin market-data gateway is not connected")
        return self._client

    async def connect(self) -> None:
        if self._connected:
            return
        if self._client is None:
            self._client = AsyncBitpin(self.config)
        if self.instrument_provider is None:
            self.instrument_provider = BitpinInstrumentProvider(
                market_loader=self._client.fetch_markets,
                currency_loader=self._client.fetch_currencies,
            )
        await self.instrument_provider.load_all_async()
        self._connected = True

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.close()
        self._connected = False

    def _symbol(self, instrument_id: InstrumentId) -> str:
        if not self._connected:
            raise GatewayStateError("BitPin market-data gateway is not connected")
        if self.instrument_provider is None:
            raise GatewayStateError("instrument provider is not initialized")
        instrument = self.instrument_provider.find(instrument_id)
        if instrument is None:
            raise KeyError(f"unknown BitPin instrument: {instrument_id}")
        return str(instrument.raw_symbol).replace("_", "/", 1)

    async def fetch_order_book(
        self,
        instrument_id: InstrumentId,
        *,
        limit: int | None = None,
    ) -> OrderBookDepth:
        symbol = self._symbol(instrument_id)
        raw = await self.client.fetch_order_book(symbol, limit=limit)
        return order_book_depth_from_bitpin(raw, instrument_id)

    async def fetch_quote(
        self,
        instrument_id: InstrumentId,
        *,
        limit: int | None = None,
    ) -> QuoteTick:
        symbol = self._symbol(instrument_id)
        raw = await self.client.fetch_order_book(symbol, limit=limit)
        return quote_tick_from_order_book(raw, instrument_id)

    async def fetch_trades(
        self,
        instrument_id: InstrumentId,
        *,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[TradeTick]:
        symbol = self._symbol(instrument_id)
        if since is None:
            raw_trades = await self.client.fetch_trades(symbol, limit=limit)
        else:
            raw_trades = await self.client.fetch_trades(
                symbol, since=since, limit=limit
            )
        return [trade_tick_from_bitpin(trade, instrument_id) for trade in raw_trades]

    async def fetch_bars(
        self,
        instrument_id: InstrumentId,
        timeframe: str,
        *,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[Bar]:
        symbol = self._symbol(instrument_id)
        if since is None:
            raw_candles = await self.client.fetch_ohlcv(
                symbol,
                timeframe,
                limit=limit,
            )
        else:
            raw_candles = await self.client.fetch_ohlcv(
                symbol,
                timeframe,
                since=since,
                limit=limit,
            )
        return [
            bar_from_bitpin(candle, instrument_id, timeframe) for candle in raw_candles
        ]

    async def watch_order_book(
        self,
        instrument_id: InstrumentId,
        *,
        timeout: float | None = None,
    ) -> OrderBookDepth:
        symbol = self._symbol(instrument_id)
        raw = await self.client.watch_order_book(symbol, timeout=timeout)
        return order_book_depth_from_bitpin(raw, instrument_id)

    async def watch_quote(
        self,
        instrument_id: InstrumentId,
        *,
        timeout: float | None = None,
    ) -> QuoteTick:
        symbol = self._symbol(instrument_id)
        raw = await self.client.watch_order_book(symbol, timeout=timeout)
        return quote_tick_from_order_book(raw, instrument_id)

    async def watch_trades(
        self,
        instrument_id: InstrumentId,
        *,
        timeout: float | None = None,
    ) -> list[TradeTick]:
        symbol = self._symbol(instrument_id)
        raw_trades = await self.client.watch_trades(symbol, timeout=timeout)
        return [trade_tick_from_bitpin(trade, instrument_id) for trade in raw_trades]

    async def watch_bars(
        self,
        instrument_id: InstrumentId,
        timeframe: str,
        *,
        timeout: float | None = None,
    ) -> list[Bar]:
        symbol = self._symbol(instrument_id)
        raw_candles = await self.client.watch_ohlcv(
            symbol,
            timeframe,
            timeout=timeout,
        )
        return [
            bar_from_bitpin(candle, instrument_id, timeframe) for candle in raw_candles
        ]
