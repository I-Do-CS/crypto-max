# Post-maintenance BitPin smoke test

Use this procedure only after BitPin confirms that the API update is complete. It is a
validation sequence, not a strategy or a production deployment procedure.

## Abort conditions

Stop without sending an order when any of these is true:

- authentication fails or the key/IP permission is unclear;
- the private suspension response is missing, malformed, or all markets are `true`;
- the selected market is not explicitly `active` and `suspended=False`;
- the market precision cannot represent the proposed amount and price exactly;
- the order rule or available balance cannot be validated from evidence;
- SDK and raw HTTP responses disagree on the same read-only request.

An all-`true` suspension map is classified as API maintenance by the current project policy,
based on BitPin support's confirmation on 2026-11-15.

## Read-only preflight

1. Record the client time, SDK version, domain, API base URL, and current Git commit.
2. Fetch currencies and markets.
3. Fetch the authenticated suspension map, balances, open orders, recent fills, and fees.
4. Repeat the suspension request through raw HTTP and compare the relevant market rows.
5. Select only a market with `active=True` and an explicit `suspended=False` result.
6. Capture the top order book and verify the proposed order's decimal increments.

Do not infer minimum order sizes from the absence of public limits. The current API docs and
live responses have not established a complete minimum-rule contract.

## One controlled order

Use a unique `clientOrderId` and a limit order placed away from the current market so it should
not execute immediately. Keep the amount within the observed free balance and use only values
that the market precision accepts. Send it through the SDK first and record the normalized and
raw response.

- If the exchange rejects it, record the HTTP status, error detail, endpoint, and client ID;
  query by that client ID to prove that no order was created.
- If the exchange accepts it, cancel immediately by the client ID, then fetch the order until
  its final state is known. Record any fill before considering the test complete.
- Never retry a timed-out create request. Reconcile by client ID first because the outcome is
  unknown.

After either result, fetch balances, open orders, and recent fills again. Keep the capture local,
mode `0600`, secret-redacted, and tagged with the SDK version, domain, and capture time.

## Direct transport comparison

Only if the SDK result is unexpected, repeat the same request through raw HTTPS using the same
domain and body. Do not switch to undocumented UI routes. A UI success is evidence of a different
service or permission path, not proof that the documented `/api/` order endpoint should be
replaced.
