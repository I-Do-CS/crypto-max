# BitPin wallet API deprecations

This record transcribes the owner-provided BitPin deprecation notice that was available during
the integration work. It is retained as project evidence; verify it against the current BitPin
notice and SDK specification before reopening wallet automation.

The notice deprecates these API v1 routes in favor of v2:

| Deprecated route | Replacement |
|---|---|
| `/api/v1/wlt/withdraws/` | `/api/v2/wlt/withdraws/` |
| `/api/v1/wlt/networks/` | `/api/v2/wlt/networks/` |
| `/api/v1/wlt/address/` | `/api/v2/wlt/address/` |
| `/api/v1/wlt/deposits/` | `/api/v2/wlt/deposits/` |

The stated migration deadline was before 31 Mordad 1405. The pinned BitPin SDK specification
already uses the v2 routes. Wallet automation remains outside the current product scope.
