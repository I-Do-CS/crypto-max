# BitPin to Nautilus integration contract

This document is the working contract for the first BitPin adapter. It records the
mapping that implementation may rely on, the capability boundary, and the exchange
behavior that still needs evidence. It is deliberately separate from market eligibility
policy.

## Contract boundary

- Venue: `BITPIN`.
- Account model: one local deployment, one BitPin account, one cash/spot account.
- Product model: spot `CurrencyPair` instruments only.
- Exchange SDK: `bitpin-sdk` 0.2.0.
- Nautilus runtime: 2.0.0rc9 custom Python adapter interfaces.
- Exchange-native identifiers remain available in adapter state and durable records.
- Credentials are runtime inputs only. They never enter this document, source control,
  fixtures, logs, or exception text.

The first probe used the SDK's `org` domain. The configured domain must remain explicit
in adapter configuration so the REST and WebSocket endpoints cannot silently diverge.

## Currencies and instruments

BitPin's unified SDK uses `BASE/QUOTE`; its raw market identifiers use `BASE_QUOTE`.
The adapter will preserve both forms:

| BitPin value | Nautilus value | Rule |
|---|---|---|
| `BASE_QUOTE` | `InstrumentId(Symbol(BASE_QUOTE), Venue("BITPIN"))` | Use the native ID as the durable instrument identity. |
| `BASE/QUOTE` | `CurrencyPair` base/quote currencies | Use the SDK's unified symbol for strategy-facing lookup. |
| BitPin currency `IRT` | custom `Currency` | Register before any `Currency.from_str("IRT")` call. |

The current Nautilus release exposes `Currency.register`, not a separate fiat-currency
class. The planned registration is:

```python
Currency.register(Currency("IRT", 0, 0, "Toman", CurrencyType.FIAT))
```

`IRT` is BitPin's exchange-native Toman code. It is not silently renamed to `IRR` or
`TMN`, and `iso4217=0` records that this exchange code is not an ISO-4217 code for the
Toman unit. `USDT` is a crypto currency with precision 2 and `BTC` is a crypto currency
with precision 8, subject to confirmation from each instrument's live currency metadata.

For each market, derive `CurrencyPair` precision from BitPin's exact tick increments:

- `price_precision` is the number of decimal places in the price increment.
- `size_precision` is the number of decimal places in the amount increment.
- `price_increment` and `size_increment` retain the exact decimal values.
- `min_quantity`, `min_notional`, and related limits stay unset when BitPin reports
  `null`; the adapter must not invent exchange rules.
- Raw BitPin market metadata is retained in `CurrencyPair.info`.

Observed on 2026-11-15:

| Market | Price increment | Amount increment | Public limits |
|---|---:|---:|---|
| `USDT/IRT` | `1` | `0.01` | all reported `null` |
| `BTC/IRT` | `1` | `0.00000001` | all reported `null` |
| `BTC/USDT` | `0.01` | `0.00000001` | all reported `null` |

BitPin returned `BTC/USDT` and not `USDT/BTC` in the first live market response. The owner
confirmed that `USDT/BTC` is a product naming alias for the canonical `BTC/USDT` market. The
alias is resolved only at the product-policy boundary; the adapter retains
`BTC_USDT.BITPIN` and never reverses prices, quantities, or order sides.

## Numeric and timestamp rules

- Keep BitPin and SDK values as `Decimal` until constructing Nautilus `Price`, `Quantity`,
  `Money`, or another typed value.
- Never route prices, amounts, balances, fees, or costs through binary floating point.
- SDK millisecond timestamps become Nautilus nanoseconds by multiplying by `1_000_000`.
- ISO timestamps are parsed with their supplied offset and normalized to UTC before the
  nanosecond conversion.
- Preserve the original timestamp and response in adapter records when the exchange gives
  both an integer and an ISO representation.

## Market data

The SDK unified methods are the parsing boundary. The adapter consumes their typed results
and keeps the SDK's raw `info` payload for diagnostics and replay fixtures.

| BitPin result | Nautilus output | Required behavior |
|---|---|---|
| ticker `bid`, `ask`, sizes, timestamp | `QuoteTick` when complete | Emit only with valid bid/ask/size values; current BitPin responses omit these fields, so use an order-book snapshot for quotes. |
| public trade `price`, `amount`, `side`, `id` | `TradeTick` | Use the BitPin trade ID; map aggressor side only after confirming BitPin side semantics. |
| OHLCV row | `Bar` with an explicit `BarType` | Keep the requested BitPin timeframe and external aggregation source. |
| REST order-book snapshot | snapshot/depth output accepted by the Nautilus book engine | Carry `nonce` when present; mark missing sequence data as a recovery concern. |
| WebSocket ticker/trade/book/candle update | corresponding streaming output | Track subscription state and resubscribe after reconnect. |

The first implementation must support public REST bootstrap and deterministic replay before
depending on WebSocket delivery. A dropped, stale, or non-monotonic book/order update causes
REST reconciliation rather than silent continuation. The first public WebSocket probe received
book and candle updates but timed out on ticker and trade updates within ten seconds; this is
an observation window result, not a capability declaration.

## Execution and account state

### Supported native operations

The SDK reports native support for spot order creation, bulk creation, single and bulk
cancellation, order reads, balances, fills, trading fees, public market data, and the
documented WebSocket streams. Native withdrawal methods are intentionally outside this
project even though the SDK exposes them.

Supported order types for the adapter contract are `limit`, `market`, and `stop_limit` once
their live request and lifecycle fields are captured. BitPin also exposes `oco`; it remains
unadvertised until its Nautilus contingency mapping and deterministic lifecycle behavior are
proven. The adapter must preserve `clientOrderId` and the BitPin order ID in both directions.

### Emulated and unsupported operations

| Nautilus capability | Contract status | Implementation rule |
|---|---|---|
| `editOrder` | emulated helper | cancel then create, with explicit non-atomic semantics and durable identity mapping. |
| `cancelAllOrders` | emulated helper | enumerate eligible open orders and issue bounded batch cancellations; never claim atomicity. |
| margin, leverage, futures, options, positions | unsupported | Do not expose them through the initial adapter. |
| withdrawals and wallet automation | unsupported by product scope | Do not call the SDK write endpoints. |
| order creation after an unknown timeout | unresolved outcome | Do not blindly retry; reconcile by client order identifier and REST state first. |

BitPin maps `initial` and `active` to open orders. A `closed` order is closed when fully
filled and canceled when not fully filled. The adapter keeps the raw state, filled amount,
remaining amount, cost, average price, and fee when producing reports.

### Account and reconciliation

- Bootstrap the main wallet with `fetch_balance` and emit one account balance per native
  currency, preserving `free`, `used`, and `total`.
- Keep balances, fills, fees, and order costs in their native currencies. USDT valuation is
  an explicit reporting conversion, never an implicit account-currency replacement.
- Reconcile open orders, recent fills, and account state after connect, reconnect, unknown
  outcomes, and restart.
- Persist venue-order ID, client-order ID, instrument ID, order state, fills, fees, and the
  last reconciliation cursor outside the unsupported database-backed custom-client path.
- Deduplicate fills by stable venue fill ID and reject stale or out-of-order state transitions
  unless reconciliation proves the newer state.

The initial Nautilus `ExecutionClient` must implement connection, disconnection, order-status
reports, fill reports, account queries, submit, query, and cancel hooks. Every network task
starts in `_connect`, is supervised with the runtime task API, and closes in `_disconnect`.

The current implementation has a standalone `BitpinMarketDataGateway` at the SDK boundary and
a v2 `BitpinDataClient` for typed market-data hooks. Their lifecycle and typed REST/WebSocket
mappings are proven with fixture-backed tests before full node integration is exercised.

Execution mappings and `BitpinStateStore` are also implemented as pure adapter foundations.
They preserve native currencies and IDs, enforce the account-balance invariant, deduplicate
fills, and retain reconciliation state across process restarts.

`BitpinExecutionGateway` is the current write boundary. It requires explicit write enablement,
checks `fetch_market_suspensions()` immediately before order creation, blocks unknown or true
flags, and records successful order identities. The live suspended-market probe was rejected by
BitPin before creation; no successful live order has been placed.

A raw HTTP probe independently reproduced the same HTTP 400 suspension response from
`POST /api/v1/odr/orders/` after direct authentication. The SDK and direct API therefore agree;
the restriction is exchange-side, and the adapter must keep the suspension preflight gate.

The owner successfully traded `0.50 USDT/IRT` through the BitPin UI while the API endpoint
continued to report every sampled market as suspended. A raw `USDT_IRT` control order was also
rejected. Treat the flag as API-orderability status for this account/path, not proof that the
market is globally halted. Automated trading remains blocked until BitPin exposes an order path
that the account can use or the API suspension state changes.

On 2026-11-15, BitPin support confirmed that an all-markets-suspended response indicates that
the API is undergoing updates. The execution policy therefore classifies an all-`true` map as
API maintenance; a partial `true` map remains a market-specific suspension signal.

The pinned SDK design limits the supported surface to `/api/` endpoints and explicitly excludes
web-app routes such as `/v1/`, `/v4/`, `/srv/`, `/crm/`, and `/tp/`, even when they expose richer
behavior. This is the leading explanation for UI/API divergence, but the exact UI route is not
part of the adapter contract and must not be guessed.

The rendered market documentation describes `tradable` and precision but does not define the
private suspension endpoint. The rendered order page also lists 5,400 create-order requests per
hour, while the pinned SDK spec lists 18,000. Keep both discrepancies explicit and configurable;
do not infer suspension state from `tradable` or silently promote one rate-limit source over the
other.

`BitpinExecutionClient` now exposes the initial Nautilus execution hooks for account bootstrap,
submit, cancel, query, and REST order/fill reports. Bulk submit, edit, cancel-all, and full node
integration testing remain open work.

## Wallet API deprecation

The owner-provided deprecation notice lists these migrations:

| Deprecated route | Replacement |
|---|---|
| `/api/v1/wlt/withdraws/` | `/api/v2/wlt/withdraws/` |
| `/api/v1/wlt/networks/` | `/api/v2/wlt/networks/` |
| `/api/v1/wlt/address/` | `/api/v2/wlt/address/` |
| `/api/v1/wlt/deposits/` | `/api/v2/wlt/deposits/` |

The pinned BitPin SDK spec already uses the replacement routes. Wallet automation and
withdrawals remain outside the current scope, so this notice does not change the active adapter
surface. Re-validate the SDK/spec before that scope is reopened.

## Errors, retries, and rate limits

- Treat authentication failures, permission failures, invalid orders, not-found responses,
  rate limits, transport failures, and timeouts as distinct classes.
- Retry only SDK-idempotent reads and only under the SDK's bounded retry policy.
- Never automatically retry order creation, cancellation, withdrawal, or any other non-
  idempotent write after an ambiguous transport outcome.
- Record endpoint, sanitized parameters, attempt number, elapsed time, error class, and
  retry decision in probe or adapter diagnostics.
- Respect the SDK's configured request buckets instead of creating an independent unbounded
  request loop.

## Evidence still required

The following items remain open and must be established from captured responses or owner
evidence before they become implementation assumptions:

1. BitPin's authoritative minimum amount, minimum notional, and other order-rule sources;
   the first public market response reported `null` limits.
2. The scope of `GET /api/v1/mkt/markets/suspensions/` (`private_get_mkt_markets_suspensions`),
   which marked every returned market suspended for this account at first capture. A live
   `BTC/USDT` order probe confirmed that its `true` flag is an actionable order gate: the
   exchange rejected a valid-precision limit order with HTTP 400 `InvalidOrder` and
   “The specified market is currently suspended.” Whether the flag is globally or account
   scoped remains undocumented, but the adapter must block writes for `true` or missing flags.
3. BitPin trade-side/aggressor semantics and the full timestamp behavior of REST and WebSocket
   events.
4. WebSocket sequence, queue-drop, reconnect, resubscription, private-auth behavior, and
   whether ticker/trade channels need a longer observation window.
5. Exact order time-in-force, stop, OCO, partial-fill, cancellation, and fee fields across
   deterministic lifecycle examples.
6. Whether a future BitPin ticker response can supply quote sizes; the first capture had
   `bid`, `ask`, and both quote sizes unset for all three observed markets.

The read-only probe harness is the evidence collection tool. Captures are local, ignored by
Git, mode `0600`, and tagged with the SDK version and capture time.
