# Crypto Max

Crypto Max is a single-user algorithmic trading platform built around NautilusTrader and the BitPin cryptocurrency exchange. The long-term objective is a reliable platform on which profitable strategies can be researched, tested, and operated safely.

Profitability is a later research outcome, not an assumption. The immediate priority is a correct, observable, and recoverable BitPin integration.

## Current status

Planning is complete for the first development phase. No BitPin credentials have been read, no live API calls have been made, and no trading orders have been sent.

The current local environment has been checked:

- Python 3.14.9
- NautilusTrader 2.0.0rc9
- BitPin SDK 0.2.0
- `uv pip check` passes
- Nautilus custom Python adapter imports are available

The reference source trees are stored under `references/`:

- `references/nautilus_trader/`
- `references/bitpin-sdk/`

## Agreed scope

### Current product scope

- One project owner, one BitPin account, and one local deployment.
- BitPin is the first and only exchange integration.
- Spot trading only for the initial product.
- The adapter should expose every BitPin spot capability that maps correctly to NautilusTrader.
- Nautilus capabilities that BitPin does not provide must remain explicit unsupported capabilities or carefully documented compatibility helpers.
- Multi-tenancy, additional exchanges, and broader product features come later.

### Initial market slice

The first end-to-end slice will cover:

- `USDT/IRT`
- `USDT/BTC`
- `BTC/IRT`

The adapter should eventually understand all valid BitPin spot markets. A separate market-eligibility policy will decide which markets are enabled for trading based on evidence such as listing age, liquidity, spread, depth, volume consistency, suspension history, available data, and verified trading rules. Market selection must not be entangled with exchange protocol translation.

### Currency and accounting policy

`IRT` is BitPin's native exchange code for its Toman-denominated markets. It is the canonical code at the exchange boundary and must not be silently converted to `IRR`, `TMN`, or another label.

- Register `IRT` as a custom Nautilus fiat currency.
- Preserve BitPin's native numeric unit, precision, and increments exactly.
- Treat `Toman` and `TMN` as descriptive aliases only unless live evidence establishes another API convention.
- Use IRT as the operational account and settlement currency.
- Preserve balances, fees, fills, and positions in their native currencies.
- Use USDT as a secondary reporting and valuation currency through explicit conversion logic.

Nautilus supports generic fiat and crypto currency pairs and custom currency registration. Multi-currency portfolio reporting will require explicit conversion prices rather than assuming every result is denominated in USDT.

### Explicitly deferred

- Strategy design and optimization.
- Profitability claims or guarantees.
- Multi-user or multi-account support.
- Additional exchanges.
- Withdrawals and other wallet automation.
- Margin, leverage, futures, options, or other non-spot products.
- Production deployment and unattended capital scaling.

## Planned development path

### 1. Define the integration contract

Record the exact mapping between BitPin and Nautilus for:

- instruments and symbols;
- currencies, precision, and increments;
- tickers, trades, candles, quotes, and order books;
- order types and order parameters;
- balances, fees, orders, fills, and account state;
- timestamps, identifiers, pagination, errors, and retry behavior;
- reconnect, resubscription, and reconciliation semantics;
- supported, emulated, and unsupported capabilities.

### 2. Build a read-only BitPin probe harness

Before implementing the adapter, use the SDK to inspect public and authenticated read-only behavior. The harness should record raw and normalized responses, request metadata, latency, pagination, rate-limit behavior, errors, and WebSocket events.

Probe captures must be local, gitignored, secret-redacted, and tagged with the SDK version, API domain, and capture time. They will become deterministic fixtures for tests. The initial probe must not place, cancel, withdraw, or otherwise mutate anything.

Credentials remain outside the repository. They must never appear in source files, fixtures, logs, commits, or issue text.

### 3. Implement an out-of-tree Nautilus adapter

The planned package will contain:

- a BitPin instrument provider;
- a Nautilus market-data client;
- a Nautilus execution client;
- adapter configuration and factories;
- REST bootstrap and reconciliation;
- WebSocket subscriptions and recovery;
- durable local records for orders, fills, and reconciliation state.

BitPin SDK objects should be translated at the adapter boundary into Nautilus domain objects. Exact decimal arithmetic must be preserved for prices, quantities, balances, fees, and costs.

### 4. Prove behavior with deterministic tests and replay

Tests must cover normal and failure paths, including:

- instrument and currency registration;
- market-data parsing and timestamp conversion;
- order submission, rejection, partial fill, fill, and cancellation;
- timeout with an unknown order outcome;
- duplicate and out-of-order events;
- dropped WebSocket updates and reconnects;
- rate limits and retry classification;
- restart reconciliation and durable identity mapping;
- native versus emulated versus unsupported capabilities.

Recorded market data and local simulation provide the first execution environment because BitPin does not provide a sandbox.

### 5. Validate in stages

The intended progression is:

1. Fixture and mock-transport tests.
2. Local replay and simulated execution.
3. Live public-data validation.
4. Live authenticated read-only validation.
5. A separately approved, very small live trading test.

No successful live write path should be inferred from a read-only test.

## Known limitations and work still required

| Limitation | Required response |
|---|---|
| BitPin has no exchange sandbox or testnet. | Use Nautilus local simulation, replay, invalid-request tests, and staged live testing. |
| BitPin currently exposes spot capabilities, not every product Nautilus supports. | Advertise the capability intersection and keep non-BitPin products out of the initial scope. |
| BitPin lacks native `editOrder` and `cancelAllOrders`. | Provide explicit cancel-replace and batched cancellation helpers only where their non-atomic behavior is acceptable. |
| Public market metadata may not include all minimum sizes or trading rules. | Discover and validate rules during probing; reject orders that cannot be validated safely. |
| The API's IRT/Toman unit, precision, and field semantics must be confirmed from real responses. | Capture and compare public, private, and order-lifecycle responses before implementation. |
| Recent fills and order history have finite retention windows. | Persist local order and fill records and reconcile after restarts. |
| BitPin WebSocket queues can drop old updates under load. | Detect gaps or stale state and reconcile from REST rather than assuming lossless delivery. |
| Custom Python clients have Nautilus runtime constraints, including unsupported Redis/PostgreSQL cache backing. | Keep the first deployment local and design explicit application persistence outside the unsupported core path. |
| Live write tests involve real capital. | Keep all write probes disabled until an explicit, separately reviewed approval and safety procedure exists. |
| The market universe can change and historical “promising” performance can be misleading. | Keep market eligibility as a separate, evidence-based policy with no look-ahead assumptions. |

## Initial integration acceptance criteria

The first integration is considered ready for controlled live testing only when it can:

- load and register `USDT/IRT`, `USDT/BTC`, and `BTC/IRT` correctly;
- preserve native BitPin symbols and exact numeric units;
- stream and record supported market data;
- bootstrap balances and open orders;
- submit and track supported spot orders through deterministic tests;
- reconcile unknown outcomes after timeouts and restarts;
- protect against duplicate fills and stale market data;
- close all adapter-owned tasks and network resources cleanly;
- document every supported, emulated, and unsupported operation;
- pass the relevant local tests without requiring live credentials.

## Project-owner participation

The project owner is an active development participant. The owner can provide evidence that is difficult to obtain remotely, including:

- contacting BitPin support;
- confirming undocumented API behavior;
- collecting physical or account-level evidence;
- validating currency-unit conventions;
- supplying decisions where the exchange behavior or product policy is ambiguous;
- approving carefully scoped live tests when the implementation is ready.

Agents should ask the owner for this evidence when documentation and captured behavior are insufficient. They should not guess at exchange semantics that can be confirmed by the owner.

## Operating rules for future agents

- Read this file before making project changes.
- Keep the scope single-user, single-account, local, and spot-only until the owner changes it.
- Do not read or copy credentials into the repository.
- Do not call live BitPin write endpoints without explicit approval for that specific test.
- Update this file whenever scope, capability support, limitations, milestones, or acceptance criteria change.
- Treat captured API behavior as evidence, and record the SDK/API version and capture date with it.

## Reference documentation

- [NautilusTrader Python adapters](https://nautilustrader.io/docs/latest/developer_guide/python_adapters/)
- [NautilusTrader instruments](https://nautilustrader.io/docs/latest/concepts/instruments/)
- [NautilusTrader positions and multi-currency accounting](https://nautilustrader.io/docs/latest/concepts/positions/)
- [BitPin SDK quickstart](https://bitpin-dev.github.io/bitpin-sdk/quickstart/)
- [BitPin SDK market data](https://bitpin-dev.github.io/bitpin-sdk/market-data/)
- [BitPin SDK trading](https://bitpin-dev.github.io/bitpin-sdk/trading/)
- [BitPin SDK WebSocket behavior](https://bitpin-dev.github.io/bitpin-sdk/websocket/)
- [BitPin SDK unsupported capabilities](https://bitpin-dev.github.io/bitpin-sdk/not-supported/)
