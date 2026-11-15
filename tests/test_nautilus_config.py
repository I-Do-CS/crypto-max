from __future__ import annotations

from pathlib import Path

from nautilus_trader.model import BarType

from crypto_max.data_client import _timeframe_from_bar_type
from crypto_max.nautilus_config import (
    BitpinDataClientConfig,
    BitpinExecutionClientConfig,
    bitpin_sdk_config,
)


def test_configs_serialize_paths_and_flags_without_secrets(tmp_path: Path) -> None:
    data_config = BitpinDataClientConfig(
        domain="ir",
        credentials_dir=tmp_path / "credentials",
    )
    execution_config = BitpinExecutionClientConfig(
        state_path=tmp_path / "state.sqlite",
        write_enabled=True,
    )

    assert data_config.dict()["domain"] == "ir"
    assert execution_config.dict()["write_enabled"] is True
    assert "secret" not in execution_config.json().decode().lower()


def test_sdk_config_is_public_by_default_and_authenticated_only_when_requested(
    tmp_path: Path,
) -> None:
    config = BitpinDataClientConfig(credentials_dir=tmp_path)
    public = bitpin_sdk_config(config, authenticated=False)
    assert public.has_credentials is False


def test_bar_type_timeframe_mapping() -> None:
    bar_type = BarType.from_str("BTC_IRT.BITPIN-5-MINUTE-LAST-EXTERNAL")
    assert _timeframe_from_bar_type(bar_type) == "5m"
