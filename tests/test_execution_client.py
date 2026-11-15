from __future__ import annotations

from crypto_max.execution_client import BitpinExecutionClient
from crypto_max.factories import BitpinExecutionClientFactory


def test_execution_client_and_factory_are_importable_v2_components() -> None:
    assert BitpinExecutionClient.__name__ == "BitpinExecutionClient"
    assert BitpinExecutionClientFactory.__name__ == "BitpinExecutionClientFactory"
