"""Durable local identity and reconciliation state for the BitPin adapter."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Self


class StateStoreError(RuntimeError):
    """Raised when durable adapter state is inconsistent or cannot be written."""


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"unsupported state value: {type(value).__name__}")


def _encode(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value, default=_json_default, sort_keys=True, separators=(",", ":")
    )


def _decode(value: str) -> dict[str, Any]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise StateStoreError("stored state payload is not an object")
    return decoded


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class BitpinStateStore:
    """SQLite-backed order, fill, and reconciliation state.

    The store contains adapter-owned exchange state only. It does not load credentials and
    never writes them. A single process may keep one connection open; reopening the same file
    is the restart-reconciliation path exercised by the tests.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()
        self.path.chmod(0o600)

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS orders (
                venue_order_id TEXT PRIMARY KEY,
                client_order_id TEXT UNIQUE,
                instrument_id TEXT NOT NULL,
                state TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fills (
                venue_fill_id TEXT PRIMARY KEY,
                venue_order_id TEXT,
                instrument_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                event_time_ns INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reconciliation (
                scope TEXT PRIMARY KEY,
                cursor TEXT,
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        self._connection.commit()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise StateStoreError("state store is closed")
        return self._connection

    def upsert_order(
        self,
        *,
        venue_order_id: str,
        client_order_id: str | None,
        instrument_id: str,
        state: str,
        payload: Mapping[str, Any],
    ) -> None:
        if not venue_order_id or not instrument_id or not state:
            raise StateStoreError(
                "venue_order_id, instrument_id, and state are required"
            )
        connection = self._require_connection()
        now = _now()
        encoded = _encode(payload)
        try:
            connection.execute(
                """
                INSERT INTO orders (
                    venue_order_id, client_order_id, instrument_id, state,
                    payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(venue_order_id) DO UPDATE SET
                    client_order_id = excluded.client_order_id,
                    instrument_id = excluded.instrument_id,
                    state = excluded.state,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    venue_order_id,
                    client_order_id,
                    instrument_id,
                    state,
                    encoded,
                    now,
                    now,
                ),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise StateStoreError(
                "client_order_id maps to more than one venue order"
            ) from exc

    def order_by_venue_id(self, venue_order_id: str) -> dict[str, Any] | None:
        row = (
            self._require_connection()
            .execute("SELECT * FROM orders WHERE venue_order_id = ?", (venue_order_id,))
            .fetchone()
        )
        return _order_row(row) if row is not None else None

    def order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        row = (
            self._require_connection()
            .execute(
                "SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,)
            )
            .fetchone()
        )
        return _order_row(row) if row is not None else None

    def record_fill(
        self,
        *,
        venue_fill_id: str,
        venue_order_id: str | None,
        instrument_id: str,
        payload: Mapping[str, Any],
        event_time_ns: int | None,
    ) -> bool:
        if not venue_fill_id or not instrument_id:
            raise StateStoreError("venue_fill_id and instrument_id are required")
        connection = self._require_connection()
        encoded = _encode(payload)
        existing = connection.execute(
            "SELECT payload_json FROM fills WHERE venue_fill_id = ?", (venue_fill_id,)
        ).fetchone()
        if existing is not None:
            if existing["payload_json"] != encoded:
                raise StateStoreError(f"fill payload changed for {venue_fill_id}")
            return False
        connection.execute(
            """
            INSERT INTO fills (
                venue_fill_id, venue_order_id, instrument_id, payload_json,
                event_time_ns, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                venue_fill_id,
                venue_order_id,
                instrument_id,
                encoded,
                event_time_ns,
                _now(),
            ),
        )
        connection.commit()
        return True

    def fills_for_order(self, venue_order_id: str) -> list[dict[str, Any]]:
        rows = (
            self._require_connection()
            .execute(
                "SELECT * FROM fills WHERE venue_order_id = ? ORDER BY event_time_ns, venue_fill_id",
                (venue_order_id,),
            )
            .fetchall()
        )
        return [_fill_row(row) for row in rows]

    def save_reconciliation(
        self,
        *,
        scope: str,
        cursor: str | None,
        state: Mapping[str, Any],
    ) -> None:
        if not scope:
            raise StateStoreError("reconciliation scope is required")
        connection = self._require_connection()
        connection.execute(
            """
            INSERT INTO reconciliation (scope, cursor, state_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET
                cursor = excluded.cursor,
                state_json = excluded.state_json,
                updated_at = excluded.updated_at
            """,
            (scope, cursor, _encode(state), _now()),
        )
        connection.commit()

    def load_reconciliation(self, scope: str) -> dict[str, Any] | None:
        row = (
            self._require_connection()
            .execute("SELECT * FROM reconciliation WHERE scope = ?", (scope,))
            .fetchone()
        )
        if row is None:
            return None
        return {
            "scope": row["scope"],
            "cursor": row["cursor"],
            "state": _decode(row["state_json"]),
            "updated_at": row["updated_at"],
        }


def _order_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "venue_order_id": row["venue_order_id"],
        "client_order_id": row["client_order_id"],
        "instrument_id": row["instrument_id"],
        "state": row["state"],
        "payload": _decode(row["payload_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _fill_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "venue_fill_id": row["venue_fill_id"],
        "venue_order_id": row["venue_order_id"],
        "instrument_id": row["instrument_id"],
        "payload": _decode(row["payload_json"]),
        "event_time_ns": row["event_time_ns"],
        "created_at": row["created_at"],
    }
