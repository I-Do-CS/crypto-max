"""Pure BitPin-to-Nautilus currency and spot-instrument mappings."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from nautilus_trader.model import (
    Currency,
    CurrencyPair,
    CurrencyType,
    InstrumentId,
    Price,
    Quantity,
    Symbol,
    Venue,
)

BITPIN_VENUE = Venue("BITPIN")
_FIAT_CODES = frozenset({"IRT"})


class MappingError(ValueError):
    """Raised when a BitPin record cannot be mapped safely."""


def _decimal(value: Any, *, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise MappingError(f"{field} must be a decimal value") from exc
    if not result.is_finite() or result <= 0:
        raise MappingError(f"{field} must be finite and greater than zero")
    return result


def decimal_places(increment: Decimal) -> int:
    """Return the decimal precision Nautilus needs for a positive increment."""

    increment = _decimal(increment, field="increment")
    exponent = increment.as_tuple().exponent
    return max(0, -exponent)


def _tick(value: Any, *, field: str) -> Decimal:
    return _decimal(value, field=field)


def currency_from_bitpin(record: Mapping[str, Any]) -> Currency:
    """Create and register a currency using BitPin's native code and precision."""

    code = str(record.get("code") or record.get("id") or "").upper()
    if not code:
        raise MappingError("currency record has no code")
    precision = decimal_places(
        _tick(record.get("precision"), field=f"{code}.precision")
    )
    name = str(record.get("name") or code)
    currency_type = CurrencyType.FIAT if code in _FIAT_CODES else CurrencyType.CRYPTO
    currency = Currency(code, precision, 0, name, currency_type)
    Currency.register(currency, overwrite=True)
    return currency


def _limit_value(limits: Mapping[str, Any], group: str, side: str) -> Decimal | None:
    group_value = limits.get(group) or {}
    value = group_value.get(side) if isinstance(group_value, Mapping) else None
    if value is None:
        return None
    return _decimal(value, field=f"limits.{group}.{side}")


def _quantity(value: Decimal | None) -> Quantity | None:
    return Quantity.from_str(format(value, "f")) if value is not None else None


def _price(value: Decimal | None) -> Price | None:
    return Price.from_str(format(value, "f")) if value is not None else None


def instrument_from_bitpin(
    record: Mapping[str, Any],
    *,
    ts_event: int = 0,
    ts_init: int = 0,
    venue: Venue = BITPIN_VENUE,
) -> CurrencyPair:
    """Map one SDK market record to a spot ``CurrencyPair``.

    The SDK's normalized market record is accepted directly. Its ``info`` mapping is
    retained as the exchange metadata carried by the Nautilus instrument.
    """

    if record.get("spot") is not True or record.get("type") not in (None, "spot"):
        raise MappingError("only BitPin spot markets are supported")
    base_code = str(record.get("baseId") or record.get("base") or "").upper()
    quote_code = str(record.get("quoteId") or record.get("quote") or "").upper()
    symbol = str(record.get("symbol") or "")
    native_id = str(record.get("id") or "")
    if not base_code or not quote_code or not symbol:
        raise MappingError("market record must include symbol, base, and quote")
    if not native_id:
        native_id = f"{base_code}_{quote_code}"

    base_currency = Currency.from_str(base_code)
    quote_currency = Currency.from_str(quote_code)
    precision = record.get("precision")
    if not isinstance(precision, Mapping):
        raise MappingError(f"{native_id}.precision must be a mapping")
    price_increment = _tick(
        precision.get("price"), field=f"{native_id}.precision.price"
    )
    size_increment = _tick(
        precision.get("amount"), field=f"{native_id}.precision.amount"
    )
    limits = record.get("limits") if isinstance(record.get("limits"), Mapping) else {}

    return CurrencyPair(
        instrument_id=InstrumentId(Symbol(native_id), venue),
        raw_symbol=Symbol(native_id),
        base_currency=base_currency,
        quote_currency=quote_currency,
        price_precision=decimal_places(price_increment),
        size_precision=decimal_places(size_increment),
        price_increment=Price.from_str(format(price_increment, "f")),
        size_increment=Quantity.from_str(format(size_increment, "f")),
        ts_event=ts_event,
        ts_init=ts_init,
        min_quantity=_quantity(_limit_value(limits, "amount", "min")),
        max_quantity=_quantity(_limit_value(limits, "amount", "max")),
        min_notional=(
            None
            if _limit_value(limits, "cost", "min") is None
            else _money(_limit_value(limits, "cost", "min"), quote_currency)
        ),
        max_notional=(
            None
            if _limit_value(limits, "cost", "max") is None
            else _money(_limit_value(limits, "cost", "max"), quote_currency)
        ),
        min_price=_price(_limit_value(limits, "price", "min")),
        max_price=_price(_limit_value(limits, "price", "max")),
        info=dict(record.get("info") or {}),
    )


def _money(value: Decimal | None, currency: Currency):
    # Imported lazily to keep the main mapping imports easy to inspect.
    from nautilus_trader.model import Money

    return Money(value, currency) if value is not None else None
