from __future__ import annotations

import asyncio
from types import SimpleNamespace

from nautilus_trader import live
from nautilus_trader.core import UUID4
from nautilus_trader.model import BarType, ClientId, InstrumentId, OrderBookDepth

from crypto_max.data_client import BitpinDataClient
from crypto_max.market_data import (
    bar_from_bitpin,
    order_book_depth_from_bitpin,
    trade_tick_from_bitpin,
)

INSTRUMENT_ID = InstrumentId.from_str("BTC_IRT.BITPIN")


class _Gateway:
    def __init__(self) -> None:
        self.depth = order_book_depth_from_bitpin(
            {
                "bids": [["1", "2"]],
                "asks": [["3", "4"]],
                "nonce": 7,
                "timestamp": 1000,
            },
            INSTRUMENT_ID,
        )

    async def fetch_order_book(
        self, instrument_id: InstrumentId, *, limit: int | None = None
    ) -> OrderBookDepth:
        assert instrument_id == INSTRUMENT_ID
        assert limit == 4
        return self.depth

    async def fetch_trades(
        self,
        instrument_id: InstrumentId,
        *,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[object]:
        assert instrument_id == INSTRUMENT_ID
        assert since == 2
        return [
            trade_tick_from_bitpin(
                {"id": "old", "price": "1", "amount": "1", "timestamp": 1},
                INSTRUMENT_ID,
            ),
            trade_tick_from_bitpin(
                {"id": "new", "price": "2", "amount": "1", "timestamp": 3},
                INSTRUMENT_ID,
            ),
        ]

    async def fetch_bars(
        self,
        instrument_id: InstrumentId,
        timeframe: str,
        *,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[object]:
        assert instrument_id == INSTRUMENT_ID
        assert timeframe == "1m"
        assert since == 2
        return [
            bar_from_bitpin([1, "1", "1", "1", "1", "1"], INSTRUMENT_ID, "1m"),
            bar_from_bitpin([3, "2", "2", "2", "2", "1"], INSTRUMENT_ID, "1m"),
        ]


def _client(responses: list[object]) -> BitpinDataClient:
    client = object.__new__(BitpinDataClient)
    client.client_id = ClientId("BITPIN")
    client.clock = SimpleNamespace(timestamp_ns=lambda: 2_000_000_000)
    client._gateway = _Gateway()
    client._handle_response = responses.append
    return client


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        instrument_id=INSTRUMENT_ID,
        depth=4,
        request_id=UUID4(),
        params=None,
    )


def _window_request() -> SimpleNamespace:
    return SimpleNamespace(
        instrument_id=INSTRUMENT_ID,
        limit=10,
        start_ns=2_000_000,
        end_ns=3_000_000,
        start=None,
        end=None,
        request_id=UUID4(),
        params=None,
    )


def test_book_snapshot_response_contains_mutable_order_book() -> None:
    responses: list[object] = []

    # Native request objects are owned by the message bus and cannot be constructed
    # directly, so the adapter contract is exercised with the same field shape.
    request = _request()
    asyncio.run(_client(responses)._request_book_snapshot(request))

    response = responses[0]
    assert isinstance(response, live.BookResponse)
    assert response.data.instrument_id == INSTRUMENT_ID
    assert response.data.sequence == 7


def test_book_depth_response_contains_a_sequence_of_depth_snapshots() -> None:
    responses: list[object] = []
    asyncio.run(_client(responses)._request_book_depth(_request()))

    response = responses[0]
    assert isinstance(response, live.BookDepthResponse)
    assert len(response.data) == 1
    assert isinstance(response.data[0], OrderBookDepth)
    assert response.data[0].sequence == 7


def test_historical_trades_forward_since_and_apply_end_window() -> None:
    responses: list[object] = []
    request = _window_request()
    asyncio.run(_client(responses)._request_trades(request))

    response = responses[0]
    assert isinstance(response, live.TradesResponse)
    assert [trade.trade_id.value for trade in response.data] == ["new"]


def test_historical_bars_forward_since_and_apply_end_window() -> None:
    responses: list[object] = []
    request = _window_request()
    request.bar_type = BarType.from_str("BTC_IRT.BITPIN-1-MINUTE-LAST-EXTERNAL")
    asyncio.run(_client(responses)._request_bars(request))

    response = responses[0]
    assert isinstance(response, live.BarsResponse)
    assert [bar.ts_event for bar in response.data] == [3_000_000]
