from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from crypto_max.probe import (
    CredentialFiles,
    HttpExchangeRecorder,
    ProbeConfigurationError,
    ProbeRecorder,
    _write_capture,
)


def test_credential_loader_reads_values_without_normalizing_internal_content(
    tmp_path: Path,
) -> None:
    (tmp_path / "api-key.txt").write_text("api-value\n", encoding="utf-8")
    (tmp_path / "secret-key.txt").write_text("secret-value\n", encoding="utf-8")

    assert CredentialFiles.from_directory(tmp_path).load() == (
        "api-value",
        "secret-value",
    )


def test_credential_loader_rejects_multiline_secret_without_exposing_it(
    tmp_path: Path,
) -> None:
    (tmp_path / "api-key.txt").write_text("api-value\n", encoding="utf-8")
    (tmp_path / "secret-key.txt").write_text(
        "secret-value\nsecond-line\n", encoding="utf-8"
    )

    with pytest.raises(ProbeConfigurationError) as error:
        CredentialFiles.from_directory(tmp_path).load()

    assert "secret-value" not in str(error.value)
    assert str(tmp_path / "secret-key.txt") in str(error.value)


def test_recorder_keeps_normalized_decimal_and_raw_info() -> None:
    recorder = ProbeRecorder()
    result = recorder.call(
        "example",
        lambda: {"price": Decimal("1.2300"), "info": {"secret_key": "do-not-store"}},
        endpoint="example_endpoint",
        parameters={"limit": 1},
        category="public",
    )

    assert result is not None
    operation = recorder.operations[0]
    assert operation["ok"] is True
    assert operation["result"]["normalized"]["price"] == "1.2300"
    assert operation["result"]["raw"]["secret_key"] == "<redacted>"


def test_capture_writer_creates_parent_and_json(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "capture.json"
    _write_capture(output, {"value": Decimal("2.50"), "api_key": "secret"})

    assert json.loads(output.read_text(encoding="utf-8")) == {
        "api_key": "<redacted>",
        "value": "2.50",
    }


def test_async_recorder_captures_awaited_result() -> None:
    recorder = ProbeRecorder()

    async def operation() -> dict[str, str]:
        return {"value": "ok"}

    asyncio.run(
        recorder.call_async(
            "async-example",
            operation,
            endpoint="ws:example",
            category="websocket-public",
        )
    )

    assert recorder.operations[0]["ok"] is True
    assert recorder.operations[0]["result"]["normalized"] == {"value": "ok"}


def test_http_exchange_recorder_captures_sanitized_metadata() -> None:
    recorder = HttpExchangeRecorder()
    recorder.active_operation = "mock.request"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            headers={"retry-after": "3", "x-secret": "omit"},
            json={"detail": "bad"},
            request=request,
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        event_hooks={
            "request": [recorder.on_request],
            "response": [recorder.on_response],
        },
    ) as client:
        client.get("https://example.test/api/v1/example/", params={"page": "1"})

    exchange = recorder.exchanges[0]
    assert exchange["operation"] == "mock.request"
    assert exchange["request"]["method"] == "GET"
    assert exchange["request"]["path"] == "/api/v1/example/"
    assert exchange["response"]["status_code"] == 400
    assert exchange["response"]["headers"]["retry-after"] == "3"
    assert "x-secret" not in exchange["response"]["headers"]
    assert "started_clock" not in exchange
