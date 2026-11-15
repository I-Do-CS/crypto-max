"""Pure BitPin execution-state mappings for Nautilus reconciliation reports."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from nautilus_trader.model import (
    AccountBalance,
    AccountId,
    ClientOrderId,
    Currency,
    FillReport,
    InstrumentId,
    LiquiditySide,
    Money,
    OrderSide,
    OrderStatus,
    OrderStatusReport,
    OrderType,
    Price,
    Quantity,
    TimeInForce,
    TradeId,
    VenueOrderId,
)

from .market_data import DataParseError, timestamp_ms_to_ns


class ExecutionMappingError(ValueError):
    """Raised when BitPin execution state cannot be represented safely."""


def _required_text(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    if value is None or str(value) == "":
        raise ExecutionMappingError(f"{key} is required")
    return str(value)


def _decimal(
    record: Mapping[str, Any], key: str, *, required: bool = True
) -> Decimal | None:
    value = record.get(key)
    if value is None:
        if required:
            raise ExecutionMappingError(f"{key} is required")
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:
        raise ExecutionMappingError(f"{key} must be a decimal value") from exc
    if not result.is_finite():
        raise ExecutionMappingError(f"{key} must be finite")
    return result


def _timestamp(record: Mapping[str, Any], key: str, fallback_ns: int) -> int:
    value = record.get(key)
    if value is None:
        return fallback_ns
    try:
        return timestamp_ms_to_ns(value, field=key)
    except DataParseError as exc:
        raise ExecutionMappingError(str(exc)) from exc


def _order_side(value: Any) -> OrderSide:
    side = str(value or "").lower()
    if side == "buy":
        return OrderSide.BUY
    if side == "sell":
        return OrderSide.SELL
    raise ExecutionMappingError(f"unsupported order side: {value!r}")


def _order_type(value: Any) -> OrderType:
    try:
        return {
            "limit": OrderType.LIMIT,
            "market": OrderType.MARKET,
            "stop_limit": OrderType.STOP_LIMIT,
        }[str(value).lower()]
    except KeyError as exc:
        raise ExecutionMappingError(
            f"unsupported BitPin order type: {value!r}"
        ) from exc


def _status(order: Mapping[str, Any]) -> OrderStatus:
    state = str(order.get("status") or "").lower()
    filled = _decimal(order, "filled", required=False) or Decimal(0)
    if state == "open":
        return OrderStatus.PARTIALLY_FILLED if filled > 0 else OrderStatus.ACCEPTED
    if state == "closed":
        return OrderStatus.FILLED
    if state == "canceled":
        return OrderStatus.CANCELED
    raise ExecutionMappingError(f"unsupported normalized order status: {state!r}")


def _optional_price(record: Mapping[str, Any], key: str) -> Price | None:
    value = _decimal(record, key, required=False)
    return Price.from_str(format(value, "f")) if value is not None else None


def order_status_report_from_bitpin(
    order: Mapping[str, Any],
    *,
    account_id: AccountId,
    instrument_id: InstrumentId,
    ts_init: int | None = None,
) -> OrderStatusReport:
    """Map one SDK-normalized order into a Nautilus reconciliation report."""

    timestamp = _timestamp(order, "timestamp", 0)
    initialized = timestamp if ts_init is None else ts_init
    amount = _decimal(order, "amount")
    filled = _decimal(order, "filled", required=False) or Decimal(0)
    return OrderStatusReport(
        account_id=account_id,
        instrument_id=instrument_id,
        venue_order_id=VenueOrderId(_required_text(order, "id")),
        order_side=_order_side(order.get("side")),
        order_type=_order_type(order.get("type")),
        time_in_force=TimeInForce.GTC,
        order_status=_status(order),
        quantity=Quantity.from_str(format(amount, "f")),
        filled_qty=Quantity.from_str(format(filled, "f")),
        ts_accepted=timestamp,
        ts_last=_timestamp(order, "lastTradeTimestamp", timestamp),
        ts_init=initialized,
        client_order_id=(
            ClientOrderId(str(order["clientOrderId"]))
            if order.get("clientOrderId")
            else None
        ),
        price=_optional_price(order, "price"),
        activation_price=_optional_price(order, "stopPrice"),
        trigger_price=_optional_price(order, "triggerPrice"),
        avg_px=_optional_price(order, "average"),
    )


def _liquidity_side(value: Any) -> LiquiditySide:
    value = str(value or "").lower()
    if value == "maker":
        return LiquiditySide.MAKER
    if value == "taker":
        return LiquiditySide.TAKER
    return LiquiditySide.NO_LIQUIDITY_SIDE


def fill_report_from_bitpin(
    trade: Mapping[str, Any],
    *,
    account_id: AccountId,
    instrument_id: InstrumentId,
    venue_order_id: VenueOrderId | None = None,
    client_order_id: ClientOrderId | None = None,
    ts_init: int | None = None,
) -> FillReport:
    """Map one SDK-normalized private fill into a Nautilus fill report."""

    order_id = venue_order_id or (
        VenueOrderId(str(trade["order"])) if trade.get("order") else None
    )
    if order_id is None:
        raise ExecutionMappingError("private fill has no order identifier")
    timestamp = _timestamp(trade, "timestamp", 0)
    fee = trade.get("fee") or {}
    fee_cost = fee.get("cost") if isinstance(fee, Mapping) else None
    commission = None
    if fee_cost is not None:
        fee_currency = fee.get("currency") if isinstance(fee, Mapping) else None
        if not fee_currency:
            raise ExecutionMappingError("fill fee has a cost but no currency")
        commission = Money(
            Decimal(str(fee_cost)),
            Currency.from_str(str(fee_currency)),
        )
    return FillReport(
        account_id=account_id,
        instrument_id=instrument_id,
        venue_order_id=order_id,
        trade_id=TradeId(_required_text(trade, "id")),
        order_side=_order_side(trade.get("side")),
        last_qty=Quantity.from_str(format(_decimal(trade, "amount"), "f")),
        last_px=Price.from_str(format(_decimal(trade, "price"), "f")),
        commission=commission,
        liquidity_side=_liquidity_side(trade.get("takerOrMaker")),
        ts_event=timestamp,
        ts_init=timestamp if ts_init is None else ts_init,
        client_order_id=client_order_id,
    )


def account_balances_from_bitpin(
    balances: Mapping[str, Any],
) -> list[AccountBalance]:
    """Map the SDK balance structure while preserving each native currency bucket."""

    result: list[AccountBalance] = []
    for code, value in balances.items():
        if code in {"free", "used", "total", "info"}:
            continue
        if not isinstance(value, Mapping):
            continue
        free = value.get("free")
        locked = value.get("used")
        total = value.get("total")
        if free is None or locked is None or total is None:
            raise ExecutionMappingError(f"balance for {code} is incomplete")
        free_decimal = Decimal(str(free))
        locked_decimal = Decimal(str(locked))
        total_decimal = Decimal(str(total))
        if total_decimal != free_decimal + locked_decimal:
            raise ExecutionMappingError(f"balance invariant failed for {code}")
        currency = Currency.from_str(str(code))
        result.append(
            AccountBalance(
                total=Money(total_decimal, currency),
                locked=Money(locked_decimal, currency),
                free=Money(free_decimal, currency),
            )
        )
    return result
