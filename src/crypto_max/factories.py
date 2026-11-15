"""Nautilus factory wiring for the BitPin market-data client."""

from __future__ import annotations

from typing import Any

from nautilus_trader.live.clients import DataClientFactory
from nautilus_trader.model import AccountId

from .data_client import BitpinDataClient
from .execution_client import BitpinExecutionClient
from .execution_gateway import BitpinExecutionGateway
from .instrument_provider import BitpinInstrumentProvider
from .mapping import BITPIN_VENUE
from .market_gateway import BitpinMarketDataGateway
from .nautilus_config import (
    BitpinDataClientConfig,
    BitpinExecutionClientConfig,
    bitpin_sdk_config,
)
from .state_store import BitpinStateStore


class BitpinDataClientFactory(DataClientFactory):
    """Construct a BitPin data client without opening resources in the factory."""

    @staticmethod
    def create(*, name: str, config: Any, cache: Any, clock: Any) -> BitpinDataClient:
        adapter_config = (
            config
            if isinstance(config, BitpinDataClientConfig)
            else BitpinDataClientConfig()
        )
        provider = BitpinInstrumentProvider()
        gateway = BitpinMarketDataGateway(
            bitpin_sdk_config(adapter_config, authenticated=False),
            instrument_provider=provider,
        )
        return BitpinDataClient(
            name=name,
            config=config,
            cache=cache,
            clock=clock,
            gateway=gateway,
            instrument_provider=provider,
            venue=BITPIN_VENUE,
        )


class BitpinExecutionClientFactory:
    """Construct the BitPin execution client with explicit write configuration."""

    @staticmethod
    def create(
        *,
        name: str,
        config: Any,
        cache: Any,
        clock: Any,
        trader_id: Any,
    ) -> BitpinExecutionClient:
        adapter_config = (
            config
            if isinstance(config, BitpinExecutionClientConfig)
            else BitpinExecutionClientConfig()
        )
        provider = BitpinInstrumentProvider()
        gateway = BitpinExecutionGateway(
            bitpin_sdk_config(adapter_config, authenticated=True),
            state_store=BitpinStateStore(adapter_config.state_path),
            instrument_provider=provider,
            write_enabled=adapter_config.write_enabled,
        )
        return BitpinExecutionClient(
            name=name,
            config=config,
            cache=cache,
            clock=clock,
            trader_id=trader_id,
            account_id=AccountId("BITPIN-ACCOUNT"),
            gateway=gateway,
            instrument_provider=provider,
            venue=BITPIN_VENUE,
        )
