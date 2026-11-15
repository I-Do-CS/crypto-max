"""Nautilus v2 market-data client backed by the BitPin market gateway."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, override

from bitpin_sdk import errors as bitpin_errors
from nautilus_trader import live
from nautilus_trader.live.clients import MarketDataClient
from nautilus_trader.model import BarType, InstrumentId, Venue

from .instrument_provider import BitpinInstrumentProvider
from .market_data import order_book_from_depth
from .market_gateway import BitpinMarketDataGateway


class BitpinDataClient(MarketDataClient):
    """Publish BitPin typed market data through Nautilus's v2 client hooks."""

    def __init__(
        self,
        *,
        name: str,
        config: Any,
        cache: Any,
        clock: Any,
        gateway: BitpinMarketDataGateway,
        instrument_provider: BitpinInstrumentProvider,
        venue: Venue,
    ) -> None:
        super().__init__(
            name=name,
            config=config,
            cache=cache,
            clock=clock,
            venue=venue,
            instrument_provider=instrument_provider,
        )
        self._gateway = gateway
        self._stream_tasks: dict[tuple[str, str], asyncio.Task[Any]] = {}

    async def _connect(self) -> None:
        await self._gateway.connect()
        for instrument in self.instrument_provider.list_all():
            self._handle_instrument(instrument)

    async def _disconnect(self) -> None:
        tasks = list(self._stream_tasks.values())
        self._stream_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._gateway.close()

    def _start_stream(
        self,
        key: tuple[str, str],
        operation: Callable[[], Awaitable[None]],
    ) -> None:
        if key not in self._stream_tasks:
            self._stream_tasks[key] = self.create_task(
                operation(), name=f"bitpin-{key[0]}-{key[1]}"
            )

    async def _stop_streams(
        self, kind: str, instrument_ids: list[InstrumentId]
    ) -> None:
        await self._stop_keys(
            [(kind, str(instrument_id)) for instrument_id in instrument_ids]
        )

    async def _stop_keys(self, keys: list[tuple[str, str]]) -> None:
        tasks = [self._stream_tasks.pop(key, None) for key in keys]
        active = [task for task in tasks if task is not None]
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)

    async def _quote_loop(self, instrument_id: InstrumentId) -> None:
        while True:
            try:
                quote = await self._gateway.watch_quote(instrument_id, timeout=30)
            except bitpin_errors.RequestTimeout:
                continue
            self._handle_data(quote)

    async def _trade_loop(self, instrument_id: InstrumentId) -> None:
        while True:
            try:
                trades = await self._gateway.watch_trades(instrument_id, timeout=30)
            except bitpin_errors.RequestTimeout:
                continue
            for trade in trades:
                self._handle_data(trade)

    async def _book_loop(self, instrument_id: InstrumentId) -> None:
        while True:
            try:
                depth = await self._gateway.watch_order_book(instrument_id, timeout=30)
            except bitpin_errors.RequestTimeout:
                continue
            self._handle_data(depth)

    async def _bar_loop(self, bar_type: BarType) -> None:
        timeframe = _timeframe_from_bar_type(bar_type)
        while True:
            try:
                bars = await self._gateway.watch_bars(
                    bar_type.instrument_id,
                    timeframe,
                    timeout=30,
                )
            except bitpin_errors.RequestTimeout:
                continue
            for bar in bars:
                self._handle_data(bar)

    @override
    async def _subscribe_quotes(self, command: live.SubscribeQuotes) -> None:
        for instrument_id in command.instrument_ids:
            self._start_stream(
                ("quotes", str(instrument_id)),
                lambda instrument_id=instrument_id: self._quote_loop(instrument_id),
            )

    @override
    async def _unsubscribe_quotes(self, command: live.UnsubscribeQuotes) -> None:
        await self._stop_streams("quotes", command.instrument_ids)

    @override
    async def _subscribe_trades(self, command: live.SubscribeTrades) -> None:
        for instrument_id in command.instrument_ids:
            self._start_stream(
                ("trades", str(instrument_id)),
                lambda instrument_id=instrument_id: self._trade_loop(instrument_id),
            )

    @override
    async def _unsubscribe_trades(self, command: live.UnsubscribeTrades) -> None:
        await self._stop_streams("trades", command.instrument_ids)

    @override
    async def _subscribe_book_depth(self, command: live.SubscribeBookDepth) -> None:
        for instrument_id in command.instrument_ids:
            self._start_stream(
                ("book_depth", str(instrument_id)),
                lambda instrument_id=instrument_id: self._book_loop(instrument_id),
            )

    @override
    async def _unsubscribe_book_depth(self, command: live.UnsubscribeBookDepth) -> None:
        await self._stop_streams("book_depth", command.instrument_ids)

    @override
    async def _subscribe_bars(self, command: live.SubscribeBars) -> None:
        for bar_type in command.bar_types:
            self._start_stream(
                ("bars", str(bar_type)),
                lambda bar_type=bar_type: self._bar_loop(bar_type),
            )

    @override
    async def _unsubscribe_bars(self, command: live.UnsubscribeBars) -> None:
        await self._stop_keys(
            [("bars", str(bar_type)) for bar_type in command.bar_types]
        )

    @override
    async def _request_instruments(self, request: live.RequestInstruments) -> None:
        response = live.InstrumentsResponse(
            client_id=self.client_id,
            venue=self.venue,
            data=self.instrument_provider.list_all(),
            correlation_id=request.request_id,
            ts_init=self.clock.timestamp_ns(),
            params=request.params,
        )
        self._handle_response(response)

    @override
    async def _request_instrument(self, request: live.RequestInstrument) -> None:
        instrument = self.instrument_provider.find(request.instrument_id)
        if instrument is None:
            raise KeyError(f"unknown BitPin instrument: {request.instrument_id}")
        response = live.InstrumentResponse(
            client_id=self.client_id,
            instrument_id=request.instrument_id,
            data=instrument,
            correlation_id=request.request_id,
            ts_init=self.clock.timestamp_ns(),
            params=request.params,
        )
        self._handle_response(response)

    @override
    async def _request_book_snapshot(self, request: live.RequestBookSnapshot) -> None:
        depth = await self._gateway.fetch_order_book(
            request.instrument_id, limit=request.depth
        )
        response = live.BookResponse(
            client_id=self.client_id,
            instrument_id=request.instrument_id,
            data=order_book_from_depth(depth),
            correlation_id=request.request_id,
            ts_init=self.clock.timestamp_ns(),
            params=request.params,
        )
        self._handle_response(response)

    @override
    async def _request_quotes(self, request: live.RequestQuotes) -> None:
        data = [await self._gateway.fetch_quote(request.instrument_id)]
        response = live.QuotesResponse(
            client_id=self.client_id,
            instrument_id=request.instrument_id,
            data=data,
            correlation_id=request.request_id,
            ts_init=self.clock.timestamp_ns(),
            params=request.params,
        )
        self._handle_response(response)

    @override
    async def _request_trades(self, request: live.RequestTrades) -> None:
        data = await self._gateway.fetch_trades(
            request.instrument_id,
            since=_since_ms(getattr(request, "start_ns", None)),
            limit=request.limit,
        )
        data = _filter_time_window(
            data,
            start_ns=getattr(request, "start_ns", None),
            end_ns=getattr(request, "end_ns", None),
        )
        response = live.TradesResponse(
            client_id=self.client_id,
            instrument_id=request.instrument_id,
            data=data,
            correlation_id=request.request_id,
            ts_init=self.clock.timestamp_ns(),
            start=request.start,
            end=request.end,
            params=request.params,
        )
        self._handle_response(response)

    @override
    async def _request_bars(self, request: live.RequestBars) -> None:
        timeframe = _timeframe_from_bar_type(request.bar_type)
        data = await self._gateway.fetch_bars(
            request.bar_type.instrument_id,
            timeframe,
            since=_since_ms(getattr(request, "start_ns", None)),
            limit=request.limit,
        )
        data = _filter_time_window(
            data,
            start_ns=getattr(request, "start_ns", None),
            end_ns=getattr(request, "end_ns", None),
        )
        response = live.BarsResponse(
            client_id=self.client_id,
            bar_type=request.bar_type,
            data=data,
            correlation_id=request.request_id,
            ts_init=self.clock.timestamp_ns(),
            start=request.start,
            end=request.end,
            params=request.params,
        )
        self._handle_response(response)

    @override
    async def _request_book_depth(self, request: live.RequestBookDepth) -> None:
        data = await self._gateway.fetch_order_book(
            request.instrument_id,
            limit=request.depth,
        )
        response = live.BookDepthResponse(
            client_id=self.client_id,
            instrument_id=request.instrument_id,
            data=[data],
            correlation_id=request.request_id,
            ts_init=self.clock.timestamp_ns(),
            params=request.params,
        )
        self._handle_response(response)

    async def _subscribe_instruments(self, command: live.SubscribeInstruments) -> None:
        for instrument in self.instrument_provider.list_all():
            self._handle_instrument(instrument)

    async def _subscribe_instrument(self, command: live.SubscribeInstrument) -> None:
        instrument = self.instrument_provider.find(command.instrument_id)
        if instrument is not None:
            self._handle_instrument(instrument)

    async def _unsubscribe_instruments(
        self, command: live.UnsubscribeInstruments
    ) -> None:
        pass

    async def _unsubscribe_instrument(
        self, command: live.UnsubscribeInstrument
    ) -> None:
        pass

    async def _subscribe_book_deltas(self, command: live.SubscribeBookDeltas) -> None:
        raise NotImplementedError("BitPin SDK provides snapshots, not book deltas")

    async def _unsubscribe_book_deltas(
        self, command: live.UnsubscribeBookDeltas
    ) -> None:
        pass

    async def _request_book_deltas(self, request: live.RequestBookDeltas) -> None:
        raise NotImplementedError("BitPin SDK provides snapshots, not book deltas")


def _timeframe_from_bar_type(bar_type: BarType) -> str:
    spec = bar_type.spec
    aggregation = spec.aggregation.name
    mapping = {
        ("MINUTE", 1): "1m",
        ("MINUTE", 5): "5m",
        ("MINUTE", 15): "15m",
        ("MINUTE", 30): "30m",
        ("HOUR", 1): "1h",
        ("HOUR", 3): "3h",
        ("HOUR", 4): "4h",
        ("HOUR", 6): "6h",
        ("DAY", 1): "1d",
        ("WEEK", 1): "1w",
        ("MONTH", 1): "1M",
    }
    try:
        return mapping[(aggregation, spec.step)]
    except KeyError as exc:
        raise ValueError(f"unsupported BitPin bar type: {bar_type}") from exc


def _since_ms(start_ns: int | None) -> int | None:
    """Convert a Nautilus nanosecond window start to the SDK's millisecond input."""

    if start_ns is None:
        return None
    if start_ns < 0:
        raise ValueError("historical request start must be non-negative")
    return start_ns // 1_000_000


def _filter_time_window(
    data: list[Any],
    *,
    start_ns: int | None,
    end_ns: int | None,
) -> list[Any]:
    """Apply Nautilus's inclusive request window to already typed records."""

    if start_ns is None and end_ns is None:
        return data
    return [
        item
        for item in data
        if (start_ns is None or item.ts_event >= start_ns)
        and (end_ns is None or item.ts_event <= end_ns)
    ]
