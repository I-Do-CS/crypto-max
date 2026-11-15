from __future__ import annotations

from decimal import Decimal

import pytest
from nautilus_trader.model import AccountId, InstrumentId

from crypto_max.execution_mapping import (
    ExecutionMappingError,
    account_balances_from_bitpin,
    fill_report_from_bitpin,
    order_status_report_from_bitpin,
)
from crypto_max.mapping import currency_from_bitpin

ACCOUNT_ID = AccountId("BITPIN-ACCOUNT")
INSTRUMENT_ID = InstrumentId.from_str("BTC_IRT.BITPIN")


def _register_currencies() -> None:
    currency_from_bitpin({"code": "IRT", "name": "Toman", "precision": Decimal(1)})
    currency_from_bitpin(
        {"code": "USDT", "name": "Tether", "precision": Decimal("0.01")}
    )
    currency_from_bitpin(
        {"code": "BTC", "name": "Bitcoin", "precision": Decimal("0.00000001")}
    )


def test_order_status_report_maps_partial_and_native_ids() -> None:
    report = order_status_report_from_bitpin(
        {
            "id": "535179385",
            "clientOrderId": "mm-bid-1",
            "timestamp": 1793090730123,
            "lastTradeTimestamp": None,
            "type": "limit",
            "side": "buy",
            "price": Decimal(3100000000),
            "stopPrice": None,
            "triggerPrice": None,
            "average": None,
            "amount": Decimal("0.001"),
            "filled": Decimal(0),
            "status": "open",
        },
        account_id=ACCOUNT_ID,
        instrument_id=INSTRUMENT_ID,
    )

    assert str(report.venue_order_id) == "535179385"
    assert str(report.client_order_id) == "mm-bid-1"
    assert report.order_status.name == "ACCEPTED"
    assert report.time_in_force.name == "GTC"
    assert report.ts_accepted == 1793090730123 * 1_000_000


def test_fill_report_preserves_fee_currency_and_unknown_liquidity() -> None:
    _register_currencies()
    report = fill_report_from_bitpin(
        {
            "id": "65375307",
            "order": "535179300",
            "timestamp": 1793091000500,
            "side": "sell",
            "price": Decimal(3160000000),
            "amount": Decimal("0.001"),
            "fee": {"cost": Decimal(11060), "currency": "IRT"},
            "takerOrMaker": None,
        },
        account_id=ACCOUNT_ID,
        instrument_id=INSTRUMENT_ID,
    )

    assert str(report.venue_order_id) == "535179300"
    assert str(report.trade_id) == "65375307"
    assert report.commission.currency.code == "IRT"
    assert report.liquidity_side.name == "NO_LIQUIDITY_SIDE"


def test_account_balances_preserve_native_currency_buckets() -> None:
    _register_currencies()
    balances = account_balances_from_bitpin(
        {
            "IRT": {
                "free": Decimal(146900000),
                "used": Decimal(3100000),
                "total": Decimal(150000000),
            },
            "BTC": {
                "free": Decimal("0.0115"),
                "used": Decimal("0.001"),
                "total": Decimal("0.0125"),
            },
        }
    )

    assert {balance.total.currency.code for balance in balances} == {"IRT", "BTC"}
    assert {str(balance.total) for balance in balances} == {
        "150000000 IRT",
        "0.01250000 BTC",
    }


def test_account_balance_invariant_is_enforced() -> None:
    _register_currencies()
    with pytest.raises(ExecutionMappingError, match="invariant"):
        account_balances_from_bitpin(
            {"IRT": {"free": Decimal(1), "used": Decimal(1), "total": Decimal(3)}}
        )
