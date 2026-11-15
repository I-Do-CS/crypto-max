from __future__ import annotations

from bitpin_sdk import errors as bitpin_errors

from crypto_max.execution_client import (
    BitpinExecutionClient,
    classify_submission_error,
)
from crypto_max.execution_mapping import ExecutionMappingError
from crypto_max.factories import BitpinExecutionClientFactory
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
