from __future__ import annotations

import asyncio
import json
from pathlib import Path

from nautilus_trader.common import Environment
from nautilus_trader.config import (
    DataClientConfig,
    InstrumentProviderConfig,
    LiveNodeConfig,
)
from nautilus_trader.live import LiveNode
from nautilus_trader.live.clients import DataClientFactory
from nautilus_trader.model import InstrumentId, TraderId, Venue
from nautilus_trader.trading import Strategy

from crypto_max.data_client import BitpinDataClient
from crypto_max.instrument_provider import BitpinInstrumentProvider
from crypto_max.market_data import quote_tick_from_order_book

PUBLIC_FIXTURE = Path(__file__).parent / "fixtures" / "bitpin" / "public.json"
INSTRUMENT_ID = InstrumentId.from_str("BTC_IRT.BITPIN")


class ReplayGateway:
    def __init__(self, provider: BitpinInstrumentProvider) -> None:
        fixture = json.loads(PUBLIC_FIXTURE.read_text(encoding="utf-8"))
        self.provider = provider
        self._sent = False
        self.quote = quote_tick_from_order_book(
            fixture["order_books"]["BTC/IRT"],
            INSTRUMENT_ID,
        )

    async def connect(self) -> None:
        await self.provider.load_all_async()

    async def close(self) -> None:
        pass

    async def watch_quote(
        self, instrument_id: InstrumentId, *, timeout: float
    ) -> object:
        assert instrument_id == INSTRUMENT_ID
        if self._sent:
            await asyncio.sleep(3600)
        self._sent = True
        await asyncio.sleep(0)
        return self.quote


class ReplayDataFactory(DataClientFactory):
    @staticmethod
    def create(
        *, name: str, config: object, cache: object, clock: object
    ) -> BitpinDataClient:
        fixture = json.loads(PUBLIC_FIXTURE.read_text(encoding="utf-8"))
        provider = BitpinInstrumentProvider(
            market_records=list(fixture["markets"].values()),
            currency_records=list(fixture["currencies"].values()),
        )
        gateway = ReplayGateway(provider)
        return BitpinDataClient(
            name=name,
            config=config,
            cache=cache,
            clock=clock,
            gateway=gateway,  # type: ignore[arg-type]
            instrument_provider=provider,
            venue=Venue("BITPIN"),
        )


class QuoteShutdownStrategy(Strategy):
    def __init__(self) -> None:
        super().__init__()
        self.received = []

    def on_start(self) -> None:
        self.subscribe_quotes(INSTRUMENT_ID)

    def on_quote(self, quote) -> None:
        self.received.append(quote)
        self.shutdown_system("replay quote received")


def test_fixture_replay_reaches_strategy_through_live_node() -> None:
    node = LiveNode.build(
        "BITPIN-REPLAY",
        LiveNodeConfig(
            environment=Environment.SANDBOX,
            trader_id=TraderId("BITPIN-REPLAY-001"),
            timeout_connection_secs=2,
            timeout_disconnection_secs=1,
            delay_post_stop_secs=0,
            data_clients={
                "BITPIN": DataClientConfig(
                    instrument_provider=InstrumentProviderConfig(load_all=True),
                ),
            },
        ),
        data_factories={"BITPIN": ReplayDataFactory},
    )
    strategy = QuoteShutdownStrategy()
    node.add_strategy(strategy)

    async def run() -> None:
        await asyncio.wait_for(node.run_async(), timeout=10)

    asyncio.run(run())

    assert len(strategy.received) == 1
    assert strategy.received[0].instrument_id == INSTRUMENT_ID
