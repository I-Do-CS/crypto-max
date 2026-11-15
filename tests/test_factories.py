from __future__ import annotations

from nautilus_trader.live.clients import DataClientFactory, ExecutionClientFactory

from crypto_max.data_client import BitpinDataClient
from crypto_max.factories import BitpinDataClientFactory, BitpinExecutionClientFactory
from crypto_max.nautilus_config import BitpinDataClientConfig


def test_data_factory_is_a_v2_factory_and_config_is_serializable() -> None:
    assert issubclass(BitpinDataClientFactory, DataClientFactory)
    assert BitpinDataClient.__name__ == "BitpinDataClient"
    config = BitpinDataClientConfig(domain="org")
    assert config.dict()["domain"] == "org"
    assert issubclass(BitpinExecutionClientFactory, ExecutionClientFactory)
