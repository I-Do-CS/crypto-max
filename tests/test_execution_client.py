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

    reports = asyncio.run(
        client._generate_fill_reports(
            SimpleNamespace(
                instrument_id=None, venue_order_id=None, start=None, end=None
            )
        )
    )

    assert len(reports) == 1
    assert calls[0]["venue_fill_id"] == "fill-1"
    assert calls[0]["venue_order_id"] == "order-1"
    assert calls[0]["instrument_id"] == str(InstrumentId.from_str("BTC_IRT.BITPIN"))
    assert calls[0]["event_time_ns"] == 1_000 * 1_000_000


def test_open_order_reports_are_persisted_by_venue_order_id() -> None:
    calls: list[dict[str, object]] = []

    class Client:
        async def fetch_open_orders(self) -> list[dict[str, object]]:
            return [
                {
                    "id": "order-1",
                    "clientOrderId": "client-1",
                    "symbol": "BTC/IRT",
                    "timestamp": 1_000,
                    "lastTradeTimestamp": None,
                    "type": "limit",
                    "side": "buy",
                    "price": "10",
                    "stopPrice": None,
                    "triggerPrice": None,
                    "average": None,
                    "amount": "0.1",
                    "filled": "0",
                    "status": "open",
                }
            ]

    class Store:
        def upsert_order(self, **kwargs: object) -> None:
            calls.append(kwargs)

    client = object.__new__(BitpinExecutionClient)
    client._gateway = SimpleNamespace(client=Client(), state_store=Store())
    client.account_id = AccountId("BITPIN-ACCOUNT")
    client.clock = SimpleNamespace(timestamp_ns=lambda: 2_000_000_000)

    reports = asyncio.run(client._generate_order_status_reports(SimpleNamespace()))

    assert len(reports) == 1
    assert calls[0]["venue_order_id"] == "order-1"
    assert calls[0]["client_order_id"] == "client-1"
    assert calls[0]["instrument_id"] == str(InstrumentId.from_str("BTC_IRT.BITPIN"))
    assert calls[0]["state"] == "open"


def test_fill_report_window_is_forwarded_and_applied() -> None:
    calls: list[dict[str, object]] = []
    records: list[dict[str, object]] = []
    currency_from_bitpin({"code": "IRT", "name": "Toman", "precision": 1})

    class Client:
        async def fetch_my_trades(
            self,
            *,
            symbol: str | None = None,
            since: int | None = None,
            limit: int | None = None,
        ) -> list[dict[str, object]]:
            calls.append({"symbol": symbol, "since": since, "limit": limit})
            return [
                {
                    "id": "fill-in",
                    "order": "order-1",
                    "symbol": "BTC/IRT",
                    "timestamp": 2_000,
                    "side": "buy",
                    "price": "10",
                    "amount": "0.1",
                    "fee": {"cost": "0", "currency": "IRT"},
                },
                {
                    "id": "fill-out",
                    "order": "order-1",
                    "symbol": "BTC/IRT",
                    "timestamp": 4_000,
                    "side": "buy",
                    "price": "10",
                    "amount": "0.1",
                    "fee": {"cost": "0", "currency": "IRT"},
                },
            ]

    class Store:
        def record_fill(self, **kwargs: object) -> None:
            records.append(kwargs)

    client = object.__new__(BitpinExecutionClient)
    client._gateway = SimpleNamespace(client=Client(), state_store=Store())
    client.account_id = AccountId("BITPIN-ACCOUNT")
    client.clock = SimpleNamespace(timestamp_ns=lambda: 2_000_000_000)
    command = SimpleNamespace(
        instrument_id=InstrumentId.from_str("BTC_IRT.BITPIN"),
        venue_order_id="order-1",
        start=2_000_000_000,
        end=3_000_000_000,
    )

    reports = asyncio.run(client._generate_fill_reports(command))

    assert calls == [{"symbol": "BTC/IRT", "since": 2_000, "limit": 250}]
    assert [str(report.trade_id) for report in reports] == ["fill-in"]
    assert [record["venue_fill_id"] for record in records] == ["fill-in", "fill-out"]
