# Crypto Max

A Bitpin spot exchange adapter for NautilusTrader. It translates Bitpin’s REST and WebSocket data into NautilusTrader instruments and market events, and uses SQLite to track order and fill identities across reconciliation runs.

## Inside the adapter

| Area | What it does |
| --- | --- |
| Market data | The [market gateway](src/crypto_max/market_gateway.py) handles exchange transport. The [data client](src/crypto_max/data_client.py) maps quotes, trades, candles and order books into framework types. |
| Execution | The [execution client](src/crypto_max/execution_client.py) maps order and fill events. The [execution gateway](src/crypto_max/execution_gateway.py) controls writes to the exchange. |
| Reconciliation | The [state store](src/crypto_max/state_store.py) persists client and venue order IDs, fill IDs and reconciliation checkpoints. |

The [integration contract](docs/bitpin-integration-contract.md) records how the adapter handles instrument IDs, native IRT pricing and exchange-specific behavior.

## Run the tests

Use Python 3.14+ and [uv](https://docs.astral.sh/uv/). From the repository root:

```bash
uv sync --locked
uv run pytest
```

Start with the [checked-in fixtures](tests/fixtures/bitpin/) and [adapter tests](tests/). Live execution is still under validation.

Independent project developed with AI assistance.
