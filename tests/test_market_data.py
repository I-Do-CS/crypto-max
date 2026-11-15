from __future__ import annotations

import json
from pathlib import Path

import pytest
from nautilus_trader.model import InstrumentId

from crypto_max.market_data import (
    DataParseError,
    bar_from_bitpin,
    order_book_depth_from_bitpin,
    quote_tick_from_order_book,
    quote_tick_from_ticker,
    timestamp_ms_to_ns,
    trade_tick_from_bitpin,
)

PUBLIC_FIXTURE = Path(__file__).parent / "fixtures" / "bitpin" / "public.json"
INSTRUMENT_ID = InstrumentId.from_str("BTC_IRT.BITPIN")


def _fixture() -> dict:
    return json.loads(PUBLIC_FIXTURE.read_text(encoding="utf-8"))


def test_timestamp_and_trade_mapping_preserve_exact_event_time() -> None:
    fixture = _fixture()
    trade = fixture["trades"]["BTC/IRT"][0]

    assert timestamp_ms_to_ns(trade["timestamp"]) == trade["timestamp"] * 1_000_000
    mapped = trade_tick_from_bitpin(trade, INSTRUMENT_ID)

    assert str(mapped.trade_id) == trade["id"]
    assert str(mapped.price) == trade["price"]
    assert str(mapped.size) == trade["amount"]
    assert mapped.ts_event == trade["timestamp"] * 1_000_000


def test_candle_mapping_uses_external_bar_type() -> None:
    candle = _fixture()["candles"]["BTC/IRT"][0]
    mapped = bar_from_bitpin(candle, INSTRUMENT_ID, "1m")

    assert str(mapped.bar_type) == "BTC_IRT.BITPIN-1-MINUTE-LAST-EXTERNAL"
    assert str(mapped.open) == candle[1]
    assert str(mapped.volume) == candle[5]
    assert mapped.ts_event == candle[0] * 1_000_000


def test_order_book_snapshot_maps_to_depth_and_top_quote() -> None:
    order_book = _fixture()["order_books"]["BTC/IRT"]
    depth = order_book_depth_from_bitpin(order_book, INSTRUMENT_ID)
    quote = quote_tick_from_order_book(order_book, INSTRUMENT_ID)

    assert len(depth.bids) == len(order_book["bids"])
    assert len(depth.asks) == len(order_book["asks"])
    assert depth.sequence == 0
    assert str(quote.bid_price) == order_book["bids"][0][0]
    assert str(quote.ask_price) == order_book["asks"][0][0]


def test_ticker_without_quote_fields_is_rejected_explicitly() -> None:
    ticker = _fixture()["tickers"]["BTC/IRT"]

    with pytest.raises(DataParseError, match="order-book snapshot"):
        quote_tick_from_ticker(ticker, INSTRUMENT_ID)
