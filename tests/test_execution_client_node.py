from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from nautilus_trader.common import Environment
from nautilus_trader.config import (
    DataClientConfig,
    ExecutionClientConfig,
    InstrumentProviderConfig,
    LiveNodeConfig,
)
from nautilus_trader.live import LiveNode
from nautilus_trader.live.clients import DataClientFactory, ExecutionClientFactory
from nautilus_trader.model import (
    AccountId,
    InstrumentId,
    OrderSide,
    Price,
    Quantity,
    TimeInForce,
    TraderId,
    Venue,
)
from nautilus_trader.trading import Strategy

from crypto_max.data_client import BitpinDataClient
from crypto_max.execution_client import BitpinExecutionClient
from crypto_max.instrument_provider import BitpinInstrumentProvider
from crypto_max.market_data import quote_tick_from_order_book
from crypto_max.state_store import BitpinStateStore

PUBLIC_FIXTURE = Path(__file__).parent / "fixtures" / "bitpin" / "public.json"
INSTRUMENT_ID = InstrumentId.from_str("BTC_IRT.BITPIN")


def _fixture() -> dict[str, Any]:
    return json.loads(PUBLIC_FIXTURE.read_text(encoding="utf-8"))


class ReplayDataGateway:
    def __init__(self, provider: BitpinInstrumentProvider) -> None:
        fixture = _fixture()
        self.provider = provider
        self.quote = quote_tick_from_order_book(
            fixture["order_books"]["BTC/IRT"], INSTRUMENT_ID
        )
        self.sent = False

    async def connect(self) -> None:
        await self.provider.load_all_async()

    async def close(self) -> None:
        pass

    async def watch_quote(
        self, instrument_id: InstrumentId, *, timeout: float
    ) -> object:
        assert instrument_id == INSTRUMENT_ID
        if self.sent:
            await asyncio.sleep(3600)
        self.sent = True
        return self.quote


class ReplayDataFactory(DataClientFactory):
    @staticmethod
    def create(
        *, name: str, config: object, cache: object, clock: object
    ) -> BitpinDataClient:
        fixture = _fixture()
        provider = BitpinInstrumentProvider(
            market_records=list(fixture["markets"].values()),
            currency_records=list(fixture["currencies"].values()),
        )
        return BitpinDataClient(
            name=name,
            config=config,
            cache=cache,
            clock=clock,
            gateway=ReplayDataGateway(provider),  # type: ignore[arg-type]
            instrument_provider=provider,
            venue=Venue("BITPIN"),
        )


class FakeExecutionSdk:
    async def fetch_balance(self) -> dict[str, Any]:
        return {
            "IRT": {
                "free": "100000000000",
                "used": "0",
                "total": "100000000000",
            },
            "BTC": {"free": "0", "used": "0", "total": "0"},
            "USDT": {"free": "100", "used": "0", "total": "100"},
        }

    async def fetch_open_orders(self) -> list[dict[str, Any]]:
        return []

    async def fetch_my_trades(self, *, limit: int) -> list[dict[str, Any]]:
        return []


class ReplayExecutionGateway:
    def __init__(self, provider: BitpinInstrumentProvider, state_path: Path) -> None:
        self.provider = provider
        self.client = FakeExecutionSdk()
        self.state_store = BitpinStateStore(state_path)
        self.write_enabled = True
        self.created: list[dict[str, Any]] = []

    async def connect(self) -> None:
        await self.provider.load_all_async()

    async def close(self) -> None:
        self.state_store.close()

    async def submit_order(self, **kwargs: Any) -> dict[str, Any]:
        order = {
            "id": "replay-order-1",
            "clientOrderId": kwargs["client_order_id"],
            "symbol": "BTC/IRT",
            "status": "open",
            "type": kwargs["order_type"],
            "side": kwargs["side"],
            "amount": kwargs["amount"],
            "price": kwargs["price"],
            "filled": "0",
            "timestamp": 1_000,
            "lastTradeTimestamp": None,
        }
        self.created.append(order)
        self.state_store.upsert_order(
            venue_order_id=order["id"],
            client_order_id=order["clientOrderId"],
            instrument_id=str(kwargs["instrument_id"]),
            state=order["status"],
            payload=order,
        )
        return order

    async def cancel_by_identifier(self, client_order_id: str) -> dict[str, Any]:
        return {"id": "replay-order-1", "clientOrderId": client_order_id}


class ReplayExecutionFactory(ExecutionClientFactory):
    @staticmethod
    def create(
        *,
        name: str,
        config: object,
        cache: object,
        clock: object,
        trader_id: TraderId,
    ) -> BitpinExecutionClient:
        fixture = _fixture()
        provider = BitpinInstrumentProvider(
            market_records=list(fixture["markets"].values()),
            currency_records=list(fixture["currencies"].values()),
        )
        gateway = ReplayExecutionGateway(
            provider, Path("/tmp/crypto-max-replay-state.sqlite")
        )
        return BitpinExecutionClient(
            name=name,
            config=config,
            cache=cache,
            clock=clock,
            trader_id=trader_id,
            account_id=AccountId("BITPIN-ACCOUNT"),
            gateway=gateway,  # type: ignore[arg-type]
            instrument_provider=provider,
            venue=Venue("BITPIN"),
        )


class SubmitOnQuoteStrategy(Strategy):
    def __init__(self) -> None:
        super().__init__()
        self.accepted = None
        self.rejected = None
        self.order = None

    def on_start(self) -> None:
        self.subscribe_quotes(INSTRUMENT_ID)

    def on_quote(self, quote) -> None:
        if self.order is None:
            self.order = self.order_factory.limit(
                INSTRUMENT_ID,
                OrderSide.BUY,
                Quantity.from_str("0.0001"),
                Price.from_str("17000000000"),
                TimeInForce.GTC,
            )
            self.submit_order(self.order)

    def on_order_accepted(self, event) -> None:
        self.accepted = event
        self.shutdown_system("replay order accepted")

    def on_order_rejected(self, event) -> None:
        self.rejected = event
        self.shutdown_system("replay order rejected")


def test_fixture_replay_reaches_execution_client_and_persists_order() -> None:
    node = LiveNode.build(
        "BITPIN-EXEC-REPLAY",
        LiveNodeConfig(
            environment=Environment.SANDBOX,
            trader_id=TraderId("BITPIN-EXEC-REPLAY-001"),
            timeout_connection_secs=2,
            timeout_reconciliation_secs=2,
            timeout_disconnection_secs=1,
            delay_post_stop_secs=0,
            data_clients={
                "BITPIN": DataClientConfig(
                    instrument_provider=InstrumentProviderConfig(load_all=True),
                ),
            },
            exec_clients={"BITPIN": ExecutionClientConfig()},
        ),
        data_factories={"BITPIN": ReplayDataFactory},
        exec_factories={"BITPIN": ReplayExecutionFactory},
    )
    strategy = SubmitOnQuoteStrategy()
    node.add_strategy(strategy)

    async def run() -> None:
        await asyncio.wait_for(node.run_async(), timeout=10)

    asyncio.run(run())

    assert strategy.accepted is not None
    assert strategy.rejected is None
    assert strategy.accepted.venue_order_id.value == "replay-order-1"
