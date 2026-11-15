from __future__ import annotations

import pytest

from crypto_max.market_policy import (
    BitpinApiMaintenanceError,
    MarketSuspensionError,
    canonical_product_symbol,
    native_market_id,
    require_orderable,
)


def test_alias_resolves_without_reversing_native_identity() -> None:
    assert canonical_product_symbol("USDT/BTC") == "BTC/USDT"
    assert native_market_id("USDT/BTC") == "BTC_USDT"
    assert canonical_product_symbol("btc_usdt") == "BTC/USDT"


def test_invalid_product_symbol_is_rejected() -> None:
    with pytest.raises(ValueError, match="invalid product symbol"):
        canonical_product_symbol("BTC")


def test_order_gate_requires_explicit_unsuspended_status() -> None:
    assert require_orderable("USDT/BTC", {"BTC/USDT": False}) == "BTC/USDT"
    with pytest.raises(MarketSuspensionError, match="suspended"):
        require_orderable("USDT/BTC", {"BTC/USDT": True})
    with pytest.raises(MarketSuspensionError, match="no suspension"):
        require_orderable("BTC/USDT", {})


def test_all_suspended_markets_are_classified_as_api_maintenance() -> None:
    with pytest.raises(BitpinApiMaintenanceError, match="under maintenance"):
        require_orderable(
            "BTC/USDT",
            {"BTC/USDT": True, "USDT/IRT": True},
        )


def test_malformed_suspension_status_is_blocked_without_truthiness_coercion() -> None:
    with pytest.raises(MarketSuspensionError, match="expected a boolean"):
        require_orderable("BTC/USDT", {"BTC/USDT": "false"})  # type: ignore[dict-item]
