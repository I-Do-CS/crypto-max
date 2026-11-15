"""Pure conversion of BitPin market-data records into Nautilus data objects."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from nautilus_trader.model import (
    AggregationSource,
    AggressorSide,
    Bar,
    BarAggregation,
    BarSpecification,
    BarType,
    BookOrder,
    BookType,
    InstrumentId,
    OrderBook,
    OrderBookDepth,
    OrderSide,
    Price,
    PriceType,
    Quantity,
    QuoteTick,
    TradeId,
    TradeTick,
)


class DataParseError(ValueError):
    """Raised when a BitPin market-data record cannot be represented safely."""


def _decimal(value: Any, *, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DataParseError(f"{field} must be a decimal value") from exc
    if not result.is_finite():
        raise DataParseError(f"{field} must be finite")
    return result


def _text(value: Any, *, field: str) -> str:
    if value is None or str(value) == "":
        raise DataParseError(f"{field} is required")
    return format(_decimal(value, field=field), "f")


def timestamp_ms_to_ns(value: Any, *, field: str = "timestamp") -> int:
    """Convert an exchange millisecond timestamp to Nautilus nanoseconds."""

    number = _decimal(value, field=field)
    if number < 0 or number != number.to_integral_value():
        raise DataParseError(f"{field} must be a non-negative integer in milliseconds")
    return int(number) * 1_000_000


def _timestamp_for(record: Mapping[str, Any], *, fallback_ns: int, field: str) -> int:
    value = record.get("timestamp")
    return fallback_ns if value is None else timestamp_ms_to_ns(value, field=field)


def _instrument_id(record: Mapping[str, Any]) -> InstrumentId:
    value = record.get("instrument_id")
    if isinstance(value, InstrumentId):
        return value
    if value is None:
        raise DataParseError("instrument_id is required")
    return InstrumentId.from_str(str(value))


def aggressor_side(value: Any) -> AggressorSide:
    side = str(value or "").lower()
    if side == "buy":
        return AggressorSide.BUY
    if side == "sell":
        return AggressorSide.SELL
    return AggressorSide.NO_AGGRESSOR


def quote_tick_from_ticker(
    ticker: Mapping[str, Any],
    instrument_id: InstrumentId,
    *,
    ts_init: int | None = None,
) -> QuoteTick:
    """Map a ticker only when it contains both prices and both quote sizes."""

    bid = ticker.get("bid")
    ask = ticker.get("ask")
    bid_size = ticker.get("bidVolume", ticker.get("bid_size"))
    ask_size = ticker.get("askVolume", ticker.get("ask_size"))
    if any(value is None for value in (bid, ask, bid_size, ask_size)):
        raise DataParseError(
            "ticker does not contain bid, ask, bid size, and ask size; use an order-book snapshot"
        )
    ts_event = _timestamp_for(ticker, fallback_ns=0, field="ticker.timestamp")
    return QuoteTick(
        instrument_id=instrument_id,
        bid_price=Price.from_str(_text(bid, field="ticker.bid")),
        ask_price=Price.from_str(_text(ask, field="ticker.ask")),
        bid_size=Quantity.from_str(_text(bid_size, field="ticker.bid_size")),
        ask_size=Quantity.from_str(_text(ask_size, field="ticker.ask_size")),
        ts_event=ts_event,
        ts_init=ts_event if ts_init is None else ts_init,
    )


def trade_tick_from_bitpin(
    trade: Mapping[str, Any],
    instrument_id: InstrumentId,
    *,
    ts_init: int | None = None,
) -> TradeTick:
    ts_event = _timestamp_for(trade, fallback_ns=0, field="trade.timestamp")
    trade_id = str(trade.get("id") or "")
    if not trade_id:
        raise DataParseError("trade.id is required")
    return TradeTick(
        instrument_id=instrument_id,
        price=Price.from_str(_text(trade.get("price"), field="trade.price")),
        size=Quantity.from_str(_text(trade.get("amount"), field="trade.amount")),
        aggressor_side=aggressor_side(trade.get("side")),
        trade_id=TradeId(trade_id),
        ts_event=ts_event,
        ts_init=ts_event if ts_init is None else ts_init,
    )


_TIMEFRAME_PARTS: dict[str, tuple[int, BarAggregation]] = {
    "1m": (1, BarAggregation.MINUTE),
    "5m": (5, BarAggregation.MINUTE),
    "15m": (15, BarAggregation.MINUTE),
    "30m": (30, BarAggregation.MINUTE),
    "1h": (1, BarAggregation.HOUR),
    "3h": (3, BarAggregation.HOUR),
    "4h": (4, BarAggregation.HOUR),
    "6h": (6, BarAggregation.HOUR),
    "1d": (1, BarAggregation.DAY),
    "1w": (1, BarAggregation.WEEK),
    "1M": (1, BarAggregation.MONTH),
}


def bar_from_bitpin(
    candle: Sequence[Any],
    instrument_id: InstrumentId,
    timeframe: str,
    *,
    ts_init: int | None = None,
) -> Bar:
    if len(candle) != 6:
        raise DataParseError(
            "candle must contain timestamp, open, high, low, close, and volume"
        )
    if timeframe not in _TIMEFRAME_PARTS:
        raise DataParseError(f"unsupported BitPin timeframe: {timeframe}")
    step, aggregation = _TIMEFRAME_PARTS[timeframe]
    ts_event = timestamp_ms_to_ns(candle[0], field="candle.timestamp")
    bar_type = BarType(
        instrument_id,
        BarSpecification(step, aggregation, PriceType.LAST),
        AggregationSource.EXTERNAL,
    )
    return Bar(
        bar_type=bar_type,
        open=Price.from_str(_text(candle[1], field="candle.open")),
        high=Price.from_str(_text(candle[2], field="candle.high")),
        low=Price.from_str(_text(candle[3], field="candle.low")),
        close=Price.from_str(_text(candle[4], field="candle.close")),
        volume=Quantity.from_str(_text(candle[5], field="candle.volume")),
        ts_event=ts_event,
        ts_init=ts_event if ts_init is None else ts_init,
    )


def order_book_depth_from_bitpin(
    order_book: Mapping[str, Any],
    instrument_id: InstrumentId,
    *,
    ts_event: int | None = None,
    ts_init: int | None = None,
) -> OrderBookDepth:
    event_ns = (
        _timestamp_for(order_book, fallback_ns=0, field="order_book.timestamp")
        if ts_event is None
        else ts_event
    )
    bids = _book_orders(order_book.get("bids"), OrderSide.BUY, "bids")
    asks = _book_orders(order_book.get("asks"), OrderSide.SELL, "asks")
    sequence = order_book.get("nonce")
    if sequence is None:
        sequence = 0
    if int(sequence) < 0:
        raise DataParseError("order_book.nonce must be non-negative")
    return OrderBookDepth(
        instrument_id=instrument_id,
        bids=bids,
        asks=asks,
        bid_counts=[1] * len(bids),
        ask_counts=[1] * len(asks),
        flags=0,
        sequence=int(sequence),
        ts_event=event_ns,
        ts_init=event_ns if ts_init is None else ts_init,
    )


def order_book_from_depth(depth: OrderBookDepth) -> OrderBook:
    """Convert one venue depth snapshot into Nautilus's mutable snapshot type."""

    book = OrderBook(depth.instrument_id, BookType.L2_MBP)
    book.apply_depth(depth)
    return book


def _book_orders(
    levels: Any,
    side: OrderSide,
    field: str,
) -> list[BookOrder]:
    if levels is None:
        raise DataParseError(f"order_book.{field} is required")
    if not isinstance(levels, Sequence) or isinstance(levels, (str, bytes)):
        raise DataParseError(f"order_book.{field} must be a sequence")
    result: list[BookOrder] = []
    for index, level in enumerate(levels, start=1):
        if not isinstance(level, Sequence) or len(level) != 2:
            raise DataParseError(
                f"order_book.{field}[{index - 1}] must be [price, amount]"
            )
        result.append(
            BookOrder(
                side,
                Price.from_str(_text(level[0], field=f"{field}[{index - 1}].price")),
                Quantity.from_str(
                    _text(level[1], field=f"{field}[{index - 1}].amount")
                ),
                index,
            )
        )
    return result


def quote_tick_from_order_book(
    order_book: Mapping[str, Any],
    instrument_id: InstrumentId,
    *,
    ts_event: int | None = None,
    ts_init: int | None = None,
) -> QuoteTick:
    depth = order_book_depth_from_bitpin(
        order_book,
        instrument_id,
        ts_event=ts_event,
        ts_init=ts_init,
    )
    if not depth.bids or not depth.asks:
        raise DataParseError("order-book snapshot needs at least one bid and ask")
    return QuoteTick(
        instrument_id=instrument_id,
        bid_price=depth.bids[0].price,
        ask_price=depth.asks[0].price,
        bid_size=depth.bids[0].size,
        ask_size=depth.asks[0].size,
        ts_event=depth.ts_event,
        ts_init=depth.ts_init,
    )
