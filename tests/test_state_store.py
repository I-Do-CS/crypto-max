from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from crypto_max.state_store import BitpinStateStore, StateStoreError


def test_state_store_persists_order_fill_and_reconciliation_across_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "bitpin.sqlite"
    with BitpinStateStore(path) as store:
        store.upsert_order(
            venue_order_id="123",
            client_order_id="bot-1",
            instrument_id="USDT_IRT.BITPIN",
            state="open",
            payload={"price": Decimal(268690), "amount": Decimal("9.30")},
        )
        assert store.record_fill(
            venue_fill_id="fill-1",
            venue_order_id="123",
            instrument_id="USDT_IRT.BITPIN",
            payload={"fee": Decimal("0.032550")},
            event_time_ns=100,
        )
        assert not store.record_fill(
            venue_fill_id="fill-1",
            venue_order_id="123",
            instrument_id="USDT_IRT.BITPIN",
            payload={"fee": Decimal("0.032550")},
            event_time_ns=100,
        )
        store.save_reconciliation(
            scope="orders",
            cursor="987",
            state={"last_seen": Decimal("1.25")},
        )

    assert path.stat().st_mode & 0o777 == 0o600
    with BitpinStateStore(path) as store:
        assert store.order_by_client_id("bot-1")["payload"] == {
            "amount": "9.30",
            "price": "268690",
        }
        assert store.fills_for_order("123")[0]["payload"] == {"fee": "0.032550"}
        assert store.load_reconciliation("orders")["state"] == {"last_seen": "1.25"}


def test_state_store_rejects_conflicting_identity_and_changed_duplicate_fill(
    tmp_path: Path,
) -> None:
    with BitpinStateStore(tmp_path / "state.sqlite") as store:
        store.upsert_order(
            venue_order_id="123",
            client_order_id="bot-1",
            instrument_id="BTC_IRT.BITPIN",
            state="open",
            payload={},
        )
        with pytest.raises(StateStoreError, match="client_order_id"):
            store.upsert_order(
                venue_order_id="456",
                client_order_id="bot-1",
                instrument_id="BTC_IRT.BITPIN",
                state="open",
                payload={},
            )
        store.record_fill(
            venue_fill_id="fill-1",
            venue_order_id="123",
            instrument_id="BTC_IRT.BITPIN",
            payload={"price": "1"},
            event_time_ns=1,
        )
        with pytest.raises(StateStoreError, match="payload changed"):
            store.record_fill(
                venue_fill_id="fill-1",
                venue_order_id="123",
                instrument_id="BTC_IRT.BITPIN",
                payload={"price": "2"},
                event_time_ns=1,
            )
