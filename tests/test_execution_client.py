from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bitpin_sdk import errors as bitpin_errors
from nautilus_trader.model import AccountId, InstrumentId

from crypto_max.execution_client import (
    BitpinExecutionClient,
    classify_cancellation_error,
    classify_submission_error,
)
from crypto_max.execution_gateway import ExecutionGatewayStateError
from crypto_max.execution_mapping import ExecutionMappingError
from crypto_max.factories import BitpinExecutionClientFactory
from crypto_max.mapping import currency_from_bitpin
from crypto_max.market_policy import MarketSuspensionError


def test_execution_client_and_factory_are_importable_v2_components() -> None:
    assert BitpinExecutionClient.__name__ == "BitpinExecutionClient"
    assert BitpinExecutionClientFactory.__name__ == "BitpinExecutionClientFactory"


def test_submission_error_classification_preserves_unknown_outcomes() -> None:
    assert classify_submission_error(MarketSuspensionError("maintenance")) == "denied"
    assert (
        classify_submission_error(bitpin_errors.InvalidOrder("bad order")) == "rejected"
    )
    assert classify_submission_error(TimeoutError("transport")) == "unknown"
    assert classify_submission_error(ExecutionMappingError("bad mapping")) == "rejected"


def test_cancellation_error_classification_preserves_unknown_outcomes() -> None:
    assert classify_cancellation_error(ExecutionGatewayStateError("disabled")) == (
        "denied"
    )
    assert classify_cancellation_error(bitpin_errors.OrderNotFound("missing")) == (
        "rejected"
    )
    assert classify_cancellation_error(TimeoutError("transport")) == "unknown"


def test_fill_reports_are_persisted_by_venue_fill_id() -> None:
    calls: list[dict[str, object]] = []
    currency_from_bitpin({"code": "IRT", "name": "Toman", "precision": 1})

    class Client:
        async def fetch_my_trades(self, *, limit: int) -> list[dict[str, object]]:
            assert limit == 250
            return [
                {
                    "id": "fill-1",
                    "order": "order-1",
                    "symbol": "BTC/IRT",
                    "timestamp": 1_000,
                    "side": "buy",
                    "price": "10",
                    "amount": "0.1",
                    "fee": {"cost": "0", "currency": "IRT"},
                }
            ]

    class Store:
        def record_fill(self, **kwargs: object) -> None:
            calls.append(kwargs)

    client = object.__new__(BitpinExecutionClient)
    client._gateway = SimpleNamespace(client=Client(), state_store=Store())
    client.account_id = AccountId("BITPIN-ACCOUNT")
    client.clock = SimpleNamespace(timestamp_ns=lambda: 2_000_000_000)

    reports = asyncio.run(client._generate_fill_reports(SimpleNamespace()))

    assert len(reports) == 1
    assert calls[0]["venue_fill_id"] == "fill-1"
    assert calls[0]["venue_order_id"] == "order-1"
    assert calls[0]["instrument_id"] == str(InstrumentId.from_str("BTC_IRT.BITPIN"))
    assert calls[0]["event_time_ns"] == 1_000 * 1_000_000
