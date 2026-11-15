"""Read-only Bitpin probe harness.

The probe intentionally has no trading or wallet-mutation operations.  It records
normalized SDK results together with the raw response retained in ``info`` and
metadata needed to turn a live observation into a deterministic local fixture.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import platform
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

import httpx
from bitpin_sdk import AsyncBitpin, Bitpin, Config, FileTokenStore
from bitpin_sdk import __version__ as bitpin_sdk_version

SCHEMA_VERSION = 1
DEFAULT_SYMBOLS = ("USDT/IRT", "USDT/BTC", "BTC/IRT")
DEFAULT_CAPTURE_DIR = Path("data/bitpin-probes")


class ProbeConfigurationError(ValueError):
    """Raised when the probe cannot safely construct its configuration."""


@dataclass(frozen=True)
class CredentialFiles:
    """Paths containing one credential each, kept outside the repository."""

    api_key: Path
    secret_key: Path

    @classmethod
    def from_directory(cls, directory: str | Path) -> CredentialFiles:
        root = Path(directory).expanduser()
        return cls(root / "api-key.txt", root / "secret-key.txt")

    def load(self) -> tuple[str, str]:
        return _read_secret(self.api_key), _read_secret(self.secret_key)


def _read_secret(path: Path) -> str:
    """Read a single-line secret without ever including it in an exception."""

    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ProbeConfigurationError(
            f"cannot read credential file {path}: {exc.strerror}"
        ) from None
    if not value:
        raise ProbeConfigurationError(f"credential file is empty: {path}")
    if any(char.isspace() for char in value):
        raise ProbeConfigurationError(
            f"credential file must contain one non-whitespace value: {path}"
        )
    return value


def _redact(value: Any) -> Any:
    """Recursively redact fields which could contain credentials or tokens."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower().replace("_", "").replace("-", "")
            if any(
                marker in lowered
                for marker in ("secret", "apikey", "token", "authorization", "password")
            ):
                result[key_text] = "<redacted>"
            else:
                result[key_text] = _redact(item)
        return result
    if isinstance(value, (list, tuple, set)):
        return [_redact(item) for item in value]
    return value


def _jsonable(value: Any) -> Any:
    """Convert SDK values, including Decimal, to stable JSON-compatible values."""

    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _raw_from_result(value: Any) -> Any:
    """Extract raw server payloads retained by the SDK under ``info``."""

    if isinstance(value, Mapping):
        if "info" in value:
            return _jsonable(value["info"])
        return {str(key): _raw_from_result(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_raw_from_result(item) for item in value]
    return None


class HttpExchangeRecorder:
    """Record sanitized request/response metadata for deterministic probe diagnostics."""

    _RESPONSE_HEADERS: ClassVar[set[str]] = {
        "content-length",
        "content-type",
        "date",
        "retry-after",
        "ratelimit-limit",
        "ratelimit-remaining",
        "ratelimit-reset",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
    }

    def __init__(self) -> None:
        self.active_operation: str | None = None
        self.exchanges: list[dict[str, Any]] = []

    def on_request(self, request: httpx.Request) -> None:
        index = len(self.exchanges)
        request.extensions["crypto_max_probe_exchange"] = index
        self.exchanges.append(
            {
                "operation": self.active_operation,
                "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "started_clock": time.perf_counter(),
                "request": {
                    "method": request.method,
                    "path": request.url.path,
                    "params": _redact(
                        _jsonable(dict(request.url.params.multi_items()))
                    ),
                    "content_length": len(request.content),
                },
            }
        )

    def on_response(self, response: httpx.Response) -> None:
        index = response.request.extensions.get("crypto_max_probe_exchange")
        if not isinstance(index, int) or index >= len(self.exchanges):
            return
        exchange = self.exchanges[index]
        started_clock = exchange.pop("started_clock", time.perf_counter())
        exchange["duration_ms"] = round((time.perf_counter() - started_clock) * 1000, 3)
        exchange["response"] = {
            "status_code": response.status_code,
            "headers": {
                key: value
                for key, value in response.headers.items()
                if key.lower() in self._RESPONSE_HEADERS
            },
            "content_length": len(response.content),
        }


class _ProbeBitpinClient:
    """Own the injected HTTP client without changing Bitpin SDK lifecycle code."""

    def __init__(self, config: Config, recorder: HttpExchangeRecorder) -> None:
        self._http_recorder = recorder
        self._http = httpx.Client(
            timeout=config.timeout,
            event_hooks={
                "request": [recorder.on_request],
                "response": [recorder.on_response],
            },
        )
        self._client = Bitpin(config, http_client=self._http)

    def __enter__(self) -> Bitpin:
        self._client.__enter__()
        return self._client

    def __exit__(self, *exc: object) -> None:
        try:
            self._client.__exit__(*exc)
        finally:
            self._http.close()


class ProbeRecorder:
    """Collect operation results without allowing secrets into the capture."""

    def __init__(self, http_recorder: HttpExchangeRecorder | None = None) -> None:
        self.operations: list[dict[str, Any]] = []
        self.http_recorder = http_recorder

    def call(
        self,
        name: str,
        operation: Callable[[], Any],
        *,
        endpoint: str,
        parameters: Mapping[str, Any] | None = None,
        category: str,
    ) -> Any | None:
        started = datetime.now(UTC)
        started_clock = time.perf_counter()
        exchange_start = (
            len(self.http_recorder.exchanges) if self.http_recorder is not None else 0
        )
        if self.http_recorder is not None:
            self.http_recorder.active_operation = name
        record: dict[str, Any] = {
            "name": name,
            "category": category,
            "endpoint": endpoint,
            "parameters": _redact(_jsonable(parameters or {})),
            "started_at": started.isoformat().replace("+00:00", "Z"),
        }
        try:
            result = operation()
        except Exception as exc:  # noqa: BLE001 - capture every operation failure
            record.update(
                {
                    "ok": False,
                    "error": {
                        "type": type(exc).__name__,
                        "message": _redact(str(exc)),
                    },
                }
            )
            result = None
        else:
            normalized = _redact(_jsonable(result))
            record.update(
                {
                    "ok": True,
                    "result": {
                        "normalized": normalized,
                        "raw": _redact(_raw_from_result(result)),
                    },
                }
            )
        if self.http_recorder is not None:
            self.http_recorder.active_operation = None
            record["http_exchanges"] = self.http_recorder.exchanges[exchange_start:]
        record["duration_ms"] = round((time.perf_counter() - started_clock) * 1000, 3)
        self.operations.append(record)
        return result

    async def call_async(
        self,
        name: str,
        operation: Callable[[], Any],
        *,
        endpoint: str,
        parameters: Mapping[str, Any] | None = None,
        category: str,
    ) -> Any | None:
        started = datetime.now(UTC)
        started_clock = time.perf_counter()
        record: dict[str, Any] = {
            "name": name,
            "category": category,
            "endpoint": endpoint,
            "parameters": _redact(_jsonable(parameters or {})),
            "started_at": started.isoformat().replace("+00:00", "Z"),
        }
        try:
            result = operation()
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001 - capture every operation failure
            record.update(
                {
                    "ok": False,
                    "error": {
                        "type": type(exc).__name__,
                        "message": _redact(str(exc)),
                    },
                }
            )
            result = None
        else:
            normalized = _redact(_jsonable(result))
            record.update(
                {
                    "ok": True,
                    "result": {
                        "normalized": normalized,
                        "raw": _redact(_raw_from_result(result)),
                    },
                }
            )
        record["duration_ms"] = round((time.perf_counter() - started_clock) * 1000, 3)
        self.operations.append(record)
        return result


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _capture_path(path: Path | None) -> Path:
    if path is not None:
        return path.expanduser()
    return DEFAULT_CAPTURE_DIR / f"probe-{_timestamp()}.json"


def _write_capture(path: Path, capture: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_redact(_jsonable(capture)), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


async def _run_public_websocket_probe(
    config: Config,
    symbol: str,
    timeout: float,
    recorder: ProbeRecorder,
) -> None:
    """Capture one update from each public stream for one selected symbol."""

    async with AsyncBitpin(config) as client:
        operations = (
            (
                "ticker",
                lambda: client.watch_ticker(symbol, timeout=timeout),
                "ws:market",
                {},
            ),
            (
                "order_book",
                lambda: client.watch_order_book(symbol, timeout=timeout),
                "ws:orderbook",
                {},
            ),
            (
                "trades",
                lambda: client.watch_trades(symbol, timeout=timeout),
                "ws:matches",
                {},
            ),
            (
                "candles",
                lambda: client.watch_ohlcv(symbol, "1m", timeout=timeout),
                "ws:market",
                {"timeframe": "1m"},
            ),
        )
        for suffix, operation, endpoint, parameters in operations:
            await recorder.call_async(
                f"websocket.{symbol.replace('/', '_')}.{suffix}",
                operation,
                endpoint=endpoint,
                parameters={"symbol": symbol, **parameters},
                category="websocket-public",
            )


def _run_public_websocket_probe_sync(
    config: Config,
    symbol: str,
    timeout: float,
    recorder: ProbeRecorder,
) -> None:
    asyncio.run(_run_public_websocket_probe(config, symbol, timeout, recorder))


def run_probe(
    *,
    config: Config,
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
    include_private: bool = True,
    include_websocket: bool = False,
    websocket_timeout: float = 10.0,
) -> dict[str, Any]:
    """Run public probes and, when configured, authenticated read-only probes."""

    http_recorder = HttpExchangeRecorder()
    recorder = ProbeRecorder(http_recorder)
    with _ProbeBitpinClient(config, http_recorder) as client:
        description = client.describe()
        markets = recorder.call(
            "public.markets",
            client.fetch_markets,
            endpoint="public_get_mkt_markets",
            category="public",
        )
        recorder.call(
            "public.currencies",
            client.fetch_currencies,
            endpoint="public_get_mkt_currencies",
            category="public",
        )
        recorder.call(
            "public.tickers",
            lambda: client.fetch_tickers(list(symbols)),
            endpoint="public_get_mkt_tickers",
            parameters={"symbols": list(symbols)},
            category="public",
        )

        available_symbols: set[str] = set()
        if isinstance(markets, list):
            available_symbols = {
                str(market.get("symbol"))
                for market in markets
                if isinstance(market, Mapping) and market.get("symbol")
            }
        for symbol in symbols:
            if available_symbols and symbol not in available_symbols:
                recorder.operations.append(
                    {
                        "name": f"public.{symbol}.skipped",
                        "category": "public",
                        "endpoint": None,
                        "parameters": {"symbol": symbol},
                        "ok": False,
                        "skipped": True,
                        "error": {
                            "type": "UnavailableMarket",
                            "message": "symbol was not listed by the markets response",
                        },
                        "duration_ms": 0,
                    }
                )
                continue
            safe_name = symbol.replace("/", "_")
            recorder.call(
                f"public.{safe_name}.order_book",
                lambda symbol=symbol: client.fetch_order_book(symbol, limit=20),
                endpoint="public_get_mth_orderbook_symbol",
                parameters={"symbol": symbol, "limit": 20},
                category="public",
            )
            recorder.call(
                f"public.{safe_name}.trades",
                lambda symbol=symbol: client.fetch_trades(symbol, limit=50),
                endpoint="public_get_mth_matches_symbol",
                parameters={"symbol": symbol, "limit": 50},
                category="public",
            )
            recorder.call(
                f"public.{safe_name}.candles",
                lambda symbol=symbol: client.fetch_ohlcv(symbol, "1m", limit=100),
                endpoint="public_get_mkt_candles",
                parameters={"symbol": symbol, "timeframe": "1m", "limit": 100},
                category="public",
            )

        if include_private:
            private_operations = (
                (
                    "private.balance",
                    client.fetch_balance,
                    "private_get_wlt_wallets",
                    {},
                ),
                (
                    "private.open_orders",
                    client.fetch_open_orders,
                    "private_get_odr_orders",
                    {},
                ),
                (
                    "private.my_trades",
                    client.fetch_my_trades,
                    "private_get_odr_fills",
                    {},
                ),
                (
                    "private.trading_fees",
                    client.fetch_trading_fees,
                    "private_get_mkt_commissions",
                    {},
                ),
                (
                    "private.market_suspensions",
                    client.fetch_market_suspensions,
                    "private_get_mkt_markets_suspensions",
                    {},
                ),
            )
            for name, operation, endpoint, parameters in private_operations:
                recorder.call(
                    name,
                    operation,
                    endpoint=endpoint,
                    parameters=parameters,
                    category="private-read-only",
                )

        if include_websocket:
            websocket_symbols = [
                symbol for symbol in symbols if symbol in available_symbols
            ]
            if websocket_symbols:
                _run_public_websocket_probe_sync(
                    config,
                    websocket_symbols[0],
                    websocket_timeout,
                    recorder,
                )

        return {
            "schema_version": SCHEMA_VERSION,
            "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "runtime": {
                "python": sys.version,
                "platform": platform.platform(),
                "bitpin_sdk": bitpin_sdk_version,
            },
            "config": {
                "domain": config.domain,
                "rest_url": config.rest_url,
                "ws_url": config.ws_url,
                "authenticated": config.has_credentials,
                "rate_limits": {
                    name: f"{rule.limit}/{rule.period}"
                    for name, rule in config.all_rate_limits().items()
                },
            },
            "client_description": _redact(_jsonable(description)),
            "operations": recorder.operations,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--credentials-dir",
        type=Path,
        default=Path.home() / "bitpin",
        help="directory containing api-key.txt and secret-key.txt",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="capture path (default: data/bitpin-probes/<timestamp>.json)",
    )
    parser.add_argument("--domain", choices=("org", "ir", "market"), default="org")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--public-only", action="store_true", help="skip authenticated read-only calls"
    )
    parser.add_argument(
        "--websocket",
        action="store_true",
        help="capture one update from each public WebSocket stream",
    )
    parser.add_argument(
        "--websocket-timeout",
        type=float,
        default=10.0,
        help="seconds to wait for each public WebSocket update",
    )
    parser.add_argument(
        "--symbol", dest="symbols", action="append", help="symbol to probe; repeatable"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    symbols = tuple(args.symbols or DEFAULT_SYMBOLS)
    credentials = None
    if not args.public_only:
        credentials = CredentialFiles.from_directory(args.credentials_dir).load()
    config = Config(
        api_key=credentials[0] if credentials else None,
        secret_key=credentials[1] if credentials else None,
        domain=args.domain,
        timeout=args.timeout,
        token_store=FileTokenStore(),
    )
    capture = run_probe(
        config=config,
        symbols=symbols,
        include_private=not args.public_only,
        include_websocket=args.websocket,
        websocket_timeout=args.websocket_timeout,
    )
    output = _capture_path(args.output)
    _write_capture(output, capture)
    failures = [
        operation
        for operation in capture["operations"]
        if not operation.get("ok") and not operation.get("skipped")
    ]
    skipped = [
        operation for operation in capture["operations"] if operation.get("skipped")
    ]
    print(f"Capture written to {output}")
    print(
        f"Recorded {len(capture['operations'])} operations; {len(failures)} failed; {len(skipped)} skipped"
    )
    if failures:
        print(
            "Failed operations: "
            + ", ".join(operation["name"] for operation in failures)
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
