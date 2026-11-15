"""Serializable Nautilus client configuration and runtime BitPin SDK configuration."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from bitpin_sdk import Config
from nautilus_trader.config import DataClientConfig, ExecutionClientConfig

from .probe import CredentialFiles

DEFAULT_CREDENTIALS_DIR = Path.home() / "bitpin"


class BitpinDataClientConfig(DataClientConfig):
    """Nautilus data-client configuration without serialized credentials."""

    def __init__(
        self,
        *,
        domain: str = "org",
        credentials_dir: str | Path = DEFAULT_CREDENTIALS_DIR,
        timeout: float = 10.0,
        rate_limits: Mapping[str, str] | None = None,
        **kwargs: object,
    ) -> None:
        # The installed PyO3 base binding cannot forward nested kwargs through a Python
        # subclass constructor. The factory injects the provider explicitly instead.
        kwargs.pop("instrument_provider", None)
        kwargs.pop("routing", None)
        kwargs.pop("handle_revised_bars", None)
        super().__init__()
        self.domain = domain
        self.credentials_dir = str(credentials_dir)
        self.timeout = float(timeout)
        self.rate_limits = dict(rate_limits or {})


class BitpinExecutionClientConfig(ExecutionClientConfig):
    """Nautilus execution configuration with an explicit local write guard."""

    def __init__(
        self,
        *,
        domain: str = "org",
        credentials_dir: str | Path = DEFAULT_CREDENTIALS_DIR,
        timeout: float = 10.0,
        state_path: str | Path = Path("data/bitpin-state.sqlite"),
        write_enabled: bool = False,
        rate_limits: Mapping[str, str] | None = None,
        **kwargs: object,
    ) -> None:
        kwargs.pop("instrument_provider", None)
        kwargs.pop("routing", None)
        super().__init__()
        self.domain = domain
        self.credentials_dir = str(credentials_dir)
        self.timeout = float(timeout)
        self.state_path = str(state_path)
        self.write_enabled = bool(write_enabled)
        self.rate_limits = dict(rate_limits or {})


def bitpin_sdk_config(
    config: BitpinDataClientConfig | BitpinExecutionClientConfig,
    *,
    authenticated: bool,
) -> Config:
    """Build an SDK config, loading credentials only when an authenticated client needs them."""

    credentials = None
    if authenticated:
        credentials = CredentialFiles.from_directory(config.credentials_dir).load()
    return Config(
        api_key=credentials[0] if credentials else None,
        secret_key=credentials[1] if credentials else None,
        domain=config.domain,  # type: ignore[arg-type]
        timeout=config.timeout,
        rate_limits=config.rate_limits,
    )
