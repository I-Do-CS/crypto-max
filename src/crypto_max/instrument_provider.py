"""Nautilus instrument provider for BitPin spot market definitions."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, override

from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.live.providers import InstrumentProvider
from nautilus_trader.model import InstrumentId

from .mapping import BITPIN_VENUE, currency_from_bitpin, instrument_from_bitpin
from .market_policy import canonical_product_symbol

RecordCollection = Sequence[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]]
RecordLoader = Callable[[], RecordCollection | Awaitable[RecordCollection]]


class BitpinInstrumentProvider(InstrumentProvider):
    """Load BitPin currencies and spot markets from injected records or async loaders.

    The provider accepts record loaders rather than constructing an SDK client itself. This
    keeps construction free of network work and lets tests use recorded fixtures. A live
    adapter can inject ``AsyncBitpin.fetch_currencies`` and ``AsyncBitpin.fetch_markets``
    wrappers without changing the provider's registration behavior.
    """

    def __init__(
        self,
        config: InstrumentProviderConfig | None = None,
        *,
        market_records: Sequence[Mapping[str, Any]] | None = None,
        currency_records: Sequence[Mapping[str, Any]] | None = None,
        market_loader: RecordLoader | None = None,
        currency_loader: RecordLoader | None = None,
    ) -> None:
        super().__init__(config)
        if market_records is not None and market_loader is not None:
            raise ValueError("provide market_records or market_loader, not both")
        if currency_records is not None and currency_loader is not None:
            raise ValueError("provide currency_records or currency_loader, not both")
        self._market_records = (
            list(market_records) if market_records is not None else None
        )
        self._currency_records = (
            list(currency_records) if currency_records is not None else None
        )
        self._market_loader = market_loader
        self._currency_loader = currency_loader
        self._records_by_id: dict[str, Mapping[str, Any]] = {}

    @staticmethod
    async def _resolve_loader(
        loader: RecordLoader | None,
    ) -> list[Mapping[str, Any]] | None:
        if loader is None:
            return None
        result = loader()
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, Mapping):
            return list(result.values())
        return list(result)

    async def _get_market_records(self) -> list[Mapping[str, Any]]:
        if self._market_records is None:
            self._market_records = await self._resolve_loader(self._market_loader)
        if self._market_records is None:
            raise RuntimeError("BitPin market records or a market_loader are required")
        return self._market_records

    async def _get_currency_records(self) -> list[Mapping[str, Any]]:
        if self._currency_records is None:
            self._currency_records = await self._resolve_loader(self._currency_loader)
        if self._currency_records is None:
            raise RuntimeError(
                "BitPin currency records or a currency_loader are required"
            )
        return self._currency_records

    def _effective_filters(self, filters: Mapping[str, Any] | None) -> dict[str, Any]:
        result = dict(self._config.filters)
        result.update(filters or {})
        return result

    @staticmethod
    def _select(
        records: Sequence[Mapping[str, Any]],
        filters: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        symbols = filters.get("symbols", filters.get("symbol"))
        if symbols is not None and isinstance(symbols, str):
            symbols = (symbols,)
        symbol_set = (
            {canonical_product_symbol(str(value)) for value in symbols}
            if symbols is not None
            else None
        )
        active = filters.get("active")
        selected: list[Mapping[str, Any]] = []
        for record in records:
            symbol = canonical_product_symbol(str(record.get("symbol") or ""))
            native_id = str(record.get("id") or "").upper()
            if (
                symbol_set is not None
                and symbol not in symbol_set
                and native_id not in symbol_set
            ):
                continue
            if active is not None and bool(record.get("active")) is not bool(active):
                continue
            selected.append(record)
        return selected

    async def _register_currencies(self) -> None:
        # Market-symbol filters must not suppress currencies required by a selected pair.
        for record in await self._get_currency_records():
            self.add_currency(currency_from_bitpin(record))

    def _add_market(self, record: Mapping[str, Any]) -> None:
        instrument = instrument_from_bitpin(record, venue=BITPIN_VENUE)
        self._records_by_id[str(instrument.id)] = record
        self.add(instrument)

    @override
    async def load_all_async(self, filters: dict | None = None) -> None:
        effective_filters = self._effective_filters(filters)
        await self._register_currencies()
        for record in self._select(await self._get_market_records(), effective_filters):
            self._add_market(record)

    @override
    async def load_ids_async(
        self,
        instrument_ids: list[InstrumentId],
        filters: dict | None = None,
    ) -> None:
        effective_filters = self._effective_filters(filters)
        await self._register_currencies()
        requested = {str(instrument_id) for instrument_id in instrument_ids}
        records = await self._get_market_records()
        for record in records:
            native_id = str(record.get("id") or "")
            candidate = f"{native_id}.BITPIN"
            if (
                candidate in requested
                and candidate not in self._records_by_id
                and self._select([record], effective_filters)
            ):
                self._add_market(record)

    @override
    async def load_async(
        self,
        instrument_id: InstrumentId,
        filters: dict | None = None,
    ) -> None:
        if self.find(instrument_id) is not None:
            return
        await self.load_ids_async([instrument_id], filters)
