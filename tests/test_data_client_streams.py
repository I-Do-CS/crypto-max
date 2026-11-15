from __future__ import annotations

import asyncio

from bitpin_sdk.errors import RequestTimeout
from nautilus_trader.model import InstrumentId

from crypto_max.data_client import BitpinDataClient


def test_stream_task_stop_uses_full_bar_type_key() -> None:
    client = object.__new__(BitpinDataClient)
    client._stream_tasks = {}

    async def sleeper() -> None:
        await asyncio.sleep(3600)

    async def run() -> None:
        task = asyncio.create_task(sleeper())
        client._stream_tasks[("bars", "BTC_IRT.BITPIN-1-MINUTE-LAST-EXTERNAL")] = task
        await client._stop_keys([("bars", "BTC_IRT.BITPIN-1-MINUTE-LAST-EXTERNAL")])
        assert task.cancelled()
        assert client._stream_tasks == {}

    asyncio.run(run())


def test_quote_stream_survives_sdk_timeout() -> None:
    client = object.__new__(BitpinDataClient)
    instrument_id = InstrumentId.from_str("BTC_IRT.BITPIN")
    received: list[object] = []

    class Gateway:
        calls = 0

        async def watch_quote(
            self, requested: InstrumentId, *, timeout: float
        ) -> object:
            assert requested == instrument_id
            assert timeout == 30
            self.calls += 1
            if self.calls == 1:
                raise RequestTimeout("quiet interval")
            if self.calls == 2:
                return "quote"
            raise asyncio.CancelledError

    gateway = Gateway()
    client._gateway = gateway
    client._handle_data = received.append

    async def run() -> None:
        task = asyncio.create_task(client._quote_loop(instrument_id))
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert gateway.calls == 3
    assert received == ["quote"]
