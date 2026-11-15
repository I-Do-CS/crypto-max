from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from bitpin_sdk import Config
from nautilus_trader.model import InstrumentId

from crypto_max.execution_gateway import (
    BitpinExecutionGateway,
    ExecutionGatewayStateError,
)
from crypto_max.instrument_provider import BitpinInstrumentProvider
from crypto_max.state_store import BitpinStateStore

PUBLIC_FIXTURE = Path(__file__).parent / "fixtures" / "bitpin" / "public.json"
INSTRUMENT_ID = InstrumentId.from_str("BTC_USDT.BITPIN")


class FakeAsyncBitpin:
    def __init__(self, *, suspended: bool) -> None:
        fixture = json.loads(PUBLIC_FIXTURE.read_text(encoding="utf-8"))
        self.markets = list(fixture["markets"].values())
        self.currencies = fixture["currencies"]
        self.suspended = suspended
        self.create_calls: list[dict[str, Any]] = []

    async def fetch_markets(self) -> list[dict[str, Any]]:
        return self.markets

    async def fetch_currencies(self) -> dict[str, dict[str, Any]]:
        return self.currencies

    async def fetch_market_suspensions(self) -> dict[str, bool]:
        return {"BTC/USDT": self.suspended}

    async def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: str | None,
        price: str | None,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        call = {
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "amount": amount,
            "price": price,
            "params": params,
        }
        self.create_calls.append(call)
        return {
            "id": "order-1",
            "clientOrderId": params["clientOrderId"],
            "symbol": symbol,
            "status": "open",
        }

    async def cancel_order_by_identifier(self, client_order_id: str) -> dict[str, Any]:
        return {"id": "order-1", "clientOrderId": client_order_id}

    async def fetch_open_orders(self) -> list[dict[str, Any]]:
        return []

    async def close(self) -> None:
        pass


def _gateway(
    tmp_path: Path, *, suspended: bool, write_enabled: bool
) -> tuple[BitpinExecutionGateway, FakeAsyncBitpin]:
    fixture = json.loads(PUBLIC_FIXTURE.read_text(encoding="utf-8"))
    provider = BitpinInstrumentProvider(
        market_records=list(fixture["markets"].values()),
        currency_records=list(fixture["currencies"].values()),
    )
    client = FakeAsyncBitpin(suspended=suspended)
    gateway = BitpinExecutionGateway(
        Config(),
        state_store=BitpinStateStore(tmp_path / "state.sqlite"),
        client=client,
        instrument_provider=provider,
        write_enabled=write_enabled,
    )
    return gateway, client


def test_suspension_gate_blocks_before_create(tmp_path: Path) -> None:
    gateway, client = _gateway(tmp_path, suspended=True, write_enabled=True)

    async def run() -> None:
        await gateway.connect()
        with pytest.raises(Exception, match="suspended"):
            await gateway.submit_order(
                instrument_id=INSTRUMENT_ID,
                order_type="limit",
                side="buy",
                amount="0.0001",
                price="60000",
                client_order_id="susp-1",
            )
        assert client.create_calls == []
        await gateway.close()

    asyncio.run(run())


def test_enabled_gateway_submits_and_persists_identity_when_unsuspended(
    tmp_path: Path,
) -> None:
    gateway, client = _gateway(tmp_path, suspended=False, write_enabled=True)

    async def run() -> None:
        await gateway.connect()
        order = await gateway.submit_order(
            instrument_id=INSTRUMENT_ID,
            order_type="limit",
            side="buy",
            amount="0.0001",
            price="60000",
            client_order_id="live-1",
        )
        assert order["id"] == "order-1"
        assert client.create_calls[0]["symbol"] == "BTC/USDT"
        assert (
            gateway.state_store.order_by_client_id("live-1")["venue_order_id"]
            == "order-1"
        )
        await gateway.cancel_by_identifier("live-1")
        await gateway.close()

    asyncio.run(run())


def test_disabled_gateway_never_submits(tmp_path: Path) -> None:
    gateway, client = _gateway(tmp_path, suspended=False, write_enabled=False)

    async def run() -> None:
        await gateway.connect()
        with pytest.raises(ExecutionGatewayStateError, match="disabled"):
            await gateway.submit_order(
                instrument_id=INSTRUMENT_ID,
                order_type="limit",
                side="buy",
                amount="0.0001",
                price="60000",
                client_order_id="disabled-1",
            )
        assert client.create_calls == []
        await gateway.close()

    asyncio.run(run())
