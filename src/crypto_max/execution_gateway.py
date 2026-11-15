"""Async BitPin execution boundary with suspension gating and local identity persistence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bitpin_sdk import AsyncBitpin, Config
from nautilus_trader.model import InstrumentId

from .instrument_provider import BitpinInstrumentProvider
from .market_policy import require_orderable
from .state_store import BitpinStateStore


class ExecutionGatewayStateError(RuntimeError):
    """Raised when execution is attempted outside the gateway lifecycle or policy."""


class BitpinExecutionGateway:
    """Translate explicitly authorized order operations into AsyncBitpin calls.

    ``write_enabled`` is a deliberate constructor guard. The gateway never starts network
    work in construction, and every submission checks the current authenticated suspension
    map immediately before the non-idempotent create call.
    """

    def __init__(
        self,
        config: Config,
        *,
        state_store: BitpinStateStore,
        client: AsyncBitpin | None = None,
        instrument_provider: BitpinInstrumentProvider | None = None,
        write_enabled: bool = False,
    ) -> None:
        self.config = config
        self.state_store = state_store
        self._client = client
        self._owns_client = client is None
        self.instrument_provider = instrument_provider
        self.write_enabled = write_enabled
        self._connected = False

    async def connect(self) -> None:
        if self._connected:
            return
        if self._client is None:
            self._client = AsyncBitpin(self.config)
        if self.instrument_provider is None:
            self.instrument_provider = BitpinInstrumentProvider(
                market_loader=self._client.fetch_markets,
                currency_loader=self._client.fetch_currencies,
            )
        await self.instrument_provider.load_all_async()
        self._connected = True

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.close()
        self._connected = False

    @property
    def client(self) -> AsyncBitpin:
        if self._client is None or not self._connected:
            raise ExecutionGatewayStateError(
                "BitPin execution gateway is not connected"
            )
        return self._client

    def _symbol(self, instrument_id: InstrumentId) -> str:
        if not self._connected or self.instrument_provider is None:
            raise ExecutionGatewayStateError(
                "BitPin execution gateway is not connected"
            )
        instrument = self.instrument_provider.find(instrument_id)
        if instrument is None:
            raise KeyError(f"unknown BitPin instrument: {instrument_id}")
        return str(instrument.raw_symbol).replace("_", "/", 1)

    async def submit_order(
        self,
        *,
        instrument_id: InstrumentId,
        order_type: str,
        side: str,
        amount: str | None,
        price: str | None,
        client_order_id: str,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        if not self.write_enabled:
            raise ExecutionGatewayStateError(
                "live writes are disabled for this gateway"
            )
        if not client_order_id:
            raise ValueError("client_order_id is required")
        symbol = self._symbol(instrument_id)
        suspension = await self.client.fetch_market_suspensions()
        canonical = require_orderable(symbol, suspension)
        order_params = {**(params or {}), "clientOrderId": client_order_id}
        order = await self.client.create_order(
            canonical,
            order_type,
            side,
            amount,
            price,
            order_params,
        )
        self.state_store.upsert_order(
            venue_order_id=str(order["id"]),
            client_order_id=str(order.get("clientOrderId") or client_order_id),
            instrument_id=str(instrument_id),
            state=str(order.get("status") or "unknown"),
            payload=dict(order),
        )
        return order

    async def cancel_by_identifier(self, client_order_id: str) -> Mapping[str, Any]:
        if not self.write_enabled:
            raise ExecutionGatewayStateError(
                "live writes are disabled for this gateway"
            )
        if not client_order_id:
            raise ValueError("client_order_id is required")
        return await self.client.cancel_order_by_identifier(client_order_id)

    async def reconcile_open_orders(self) -> list[Mapping[str, Any]]:
        orders = await self.client.fetch_open_orders()
        for order in orders:
            instrument_id = str(order.get("symbol") or "")
            self.state_store.upsert_order(
                venue_order_id=str(order["id"]),
                client_order_id=(
                    str(order["clientOrderId"]) if order.get("clientOrderId") else None
                ),
                instrument_id=instrument_id,
                state=str(order.get("status") or "unknown"),
                payload=dict(order),
            )
        return orders
