"""Explicit product-level market naming policy, separate from protocol mapping."""

from __future__ import annotations

PRODUCT_SYMBOL_ALIASES = {"USDT/BTC": "BTC/USDT"}


class MarketSuspensionError(RuntimeError):
    """Raised when an order is attempted without a confirmed unsuspended market."""


class BitpinApiMaintenanceError(MarketSuspensionError):
    """Raised when Bitpin reports every market suspended during an API update."""


def canonical_product_symbol(symbol: str) -> str:
    """Normalize a product symbol and resolve only the approved naming aliases."""

    normalized = str(symbol).strip().upper().replace("_", "/")
    parts = normalized.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"invalid product symbol: {symbol!r}")
    return PRODUCT_SYMBOL_ALIASES.get(normalized, normalized)


def native_market_id(symbol: str) -> str:
    """Return the BitPin-style ID for a canonical product symbol."""

    return canonical_product_symbol(symbol).replace("/", "_", 1)


def require_orderable(symbol: str, suspensions: dict[str, bool]) -> str:
    """Require an explicit ``False`` suspension flag before a write operation.

    A missing flag is treated as unknown and blocks the write. This is an execution
    safety gate, separate from the broader market-eligibility policy.
    """

    normalized = {
        canonical_product_symbol(key): bool(value) for key, value in suspensions.items()
    }
    canonical = canonical_product_symbol(symbol)
    if normalized and all(normalized.values()):
        raise BitpinApiMaintenanceError(
            "Bitpin reports every market suspended; treating the API as under maintenance"
        )
    if canonical not in normalized:
        raise MarketSuspensionError(f"no suspension status for {canonical}")
    if normalized[canonical]:
        raise MarketSuspensionError(f"BitPin reports {canonical} as suspended")
    return canonical
