from __future__ import annotations

import asyncio

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
