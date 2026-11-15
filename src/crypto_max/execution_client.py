"""Nautilus v2 execution client backed by the guarded BitPin execution gateway."""

from __future__ import annotations

from typing import Any, Literal, override

from bitpin_sdk import errors as bitpin_errors
from nautilus_trader import live, model
from nautilus_trader.live.clients import ExecutionClient
from nautilus_trader.model import (
    AccountId,
    AccountType,
    InstrumentId,
    LiquiditySide,
    OmsType,
    OrderType,
    TraderId,
    Venue,
    VenueOrderId,
)

from .execution_gateway import BitpinExecutionGateway, ExecutionGatewayStateError
from .execution_mapping import (
    ExecutionMappingError,
    account_balances_from_bitpin,
    fill_report_from_bitpin,
    order_status_report_from_bitpin,
)
from .market_policy import (
    MarketSuspensionError,
    canonical_product_symbol,
    native_market_id,
)

SubmissionErrorKind = Literal["denied", "rejected", "unknown"]


def classify_submission_error(error: Exception) -> SubmissionErrorKind:
    """Classify deterministic order failures without hiding ambiguous transport outcomes."""

    if isinstance(error, MarketSuspensionError):
        return "denied"
    if isinstance(
        error,
        (
            ExecutionMappingError,
            bitpin_errors.BadRequest,
            bitpin_errors.InsufficientFunds,
            bitpin_errors.InvalidOrder,
            bitpin_errors.PermissionDenied,
        ),
    ):
        return "rejected"
    return "unknown"


def classify_cancellation_error(error: Exception) -> SubmissionErrorKind:
    """Classify cancellation failures without hiding an ambiguous venue outcome."""

    if isinstance(error, ExecutionGatewayStateError):
        return "denied"
    if isinstance(
        error,
        (
            bitpin_errors.BadRequest,
            bitpin_errors.InvalidOrder,
            bitpin_errors.OrderNotFound,
            bitpin_errors.PermissionDenied,
        ),
    ):
        return "rejected"
    return "unknown"


class BitpinExecutionClient(ExecutionClient):
    """Execution hooks with explicit account and write-policy boundaries."""

    def __init__(
        self,
        *,
        name: str,
        config: Any,
        cache: Any,
        clock: Any,
        trader_id: TraderId,
        account_id: AccountId,
        gateway: BitpinExecutionGateway,
        instrument_provider: Any,
        venue: Venue,
    ) -> None:
        super().__init__(
            name=name,
            config=config,
            cache=cache,
            clock=clock,
            trader_id=trader_id,
            venue=venue,
            account_id=account_id,
            account_type=AccountType.CASH,
            oms_type=OmsType.NETTING,
            instrument_provider=instrument_provider,
        )
        self._gateway = gateway

    async def _connect(self) -> None:
        await self._gateway.connect()
        await self._emit_account_state()

    async def _disconnect(self) -> None:
        await self._gateway.close()

    async def _emit_account_state(self) -> None:
        balances = await self._gateway.client.fetch_balance()
        self.generate_account_state(
            balances=account_balances_from_bitpin(balances),
            margins=[],
            reported=True,
            ts_event=self.clock.timestamp_ns(),
        )

    def _instrument_id_for_symbol(self, symbol: str) -> InstrumentId:
        native = native_market_id(canonical_product_symbol(symbol))
        return InstrumentId.from_str(f"{native}.BITPIN")

    @staticmethod
    def _order_arguments(order: Any) -> tuple[str, str, str, str | None]:
        order_type = {
            OrderType.LIMIT: "limit",
            OrderType.MARKET: "market",
            OrderType.STOP_LIMIT: "stop_limit",
        }.get(order.order_type)
        if order_type is None:
            raise ExecutionMappingError(
                f"unsupported Nautilus order type: {order.order_type}"
            )
        return (
            order_type,
            order.side.name.lower(),
            str(order.quantity),
            (str(order.price) if order.price is not None else None),
        )

    @override
    async def _submit_order(self, command: live.SubmitOrder) -> None:
        order = command.order
        try:
            order_type, side, amount, price = self._order_arguments(order)
            created = await self._gateway.submit_order(
                instrument_id=order.instrument_id,
                order_type=order_type,
                side=side,
                amount=amount,
                price=price,
                client_order_id=str(order.client_order_id),
                params=dict(command.params or {}),
            )
        except Exception as exc:
            kind = classify_submission_error(exc)
            if kind == "denied":
                self.generate_order_denied(order, str(exc))
                return
            if kind == "rejected":
                self.generate_order_rejected(
                    order, str(exc), self.clock.timestamp_ns(), False
                )
                return
            raise
        self.generate_order_submitted(order)
        self.generate_order_accepted(
            order,
            VenueOrderId(str(created["id"])),
            self.clock.timestamp_ns(),
        )

    @override
    async def _cancel_order(self, command: live.CancelOrder) -> None:
        order = self.cache.order(command.client_order_id)
        try:
            await self._gateway.cancel_by_identifier(str(command.client_order_id))
        except Exception as exc:
            kind = classify_cancellation_error(exc)
            if kind == "denied":
                self.generate_order_cancel_denied(
                    order, str(exc), self.clock.timestamp_ns()
                )
            elif kind == "rejected":
                self.generate_order_cancel_rejected(
                    order,
                    command.venue_order_id,
                    str(exc),
                    self.clock.timestamp_ns(),
                )
            else:
                raise
            return
        self.generate_order_canceled(
            order, command.venue_order_id, self.clock.timestamp_ns()
        )

    @override
    async def _query_account(self, command: live.QueryAccount) -> None:
        await self._emit_account_state()

    @override
    async def _query_order(self, command: live.QueryOrder) -> None:
        raw = await self._gateway.client.fetch_order(str(command.venue_order_id))
        report = order_status_report_from_bitpin(
            raw,
            account_id=self.account_id,
            instrument_id=command.instrument_id,
            ts_init=self.clock.timestamp_ns(),
        )
        self._persist_order(raw, command.instrument_id)
        self._handle_report(report)

    @override
    async def _generate_order_status_report(
        self,
        command: live.GenerateOrderStatusReport,
    ) -> model.OrderStatusReport | None:
        raw = await self._gateway.client.fetch_order(str(command.venue_order_id))
        report = order_status_report_from_bitpin(
            raw,
            account_id=self.account_id,
            instrument_id=command.instrument_id,
            ts_init=self.clock.timestamp_ns(),
        )
        self._persist_order(raw, command.instrument_id)
        return report

    @override
    async def _generate_order_status_reports(
        self,
        command: live.GenerateOrderStatusReports,
    ) -> list[model.OrderStatusReport]:
        reports = []
        for order in await self._gateway.client.fetch_open_orders():
            instrument_id = self._instrument_id_for_symbol(str(order["symbol"]))
            reports.append(
                order_status_report_from_bitpin(
                    order,
                    account_id=self.account_id,
                    instrument_id=instrument_id,
                    ts_init=self.clock.timestamp_ns(),
                )
            )
            self._persist_order(order, instrument_id)
        return reports

    def _persist_order(
        self, order: dict[str, Any], instrument_id: InstrumentId
    ) -> None:
        self._gateway.state_store.upsert_order(
            venue_order_id=str(order["id"]),
            client_order_id=(
                str(order["clientOrderId"])
                if order.get("clientOrderId") is not None
                else None
            ),
            instrument_id=str(instrument_id),
            state=str(order.get("status") or "unknown"),
            payload=dict(order),
        )

    @override
    async def _generate_fill_reports(
        self,
        command: live.GenerateFillReports,
    ) -> list[model.FillReport]:
        reports = []
        params: dict[str, Any] = {"limit": 250}
        if command.instrument_id is not None:
            params["symbol"] = _native_symbol(command.instrument_id)
        if command.start is not None:
            if command.start < 0:
                raise ValueError("fill report start must be non-negative")
            params["since"] = command.start // 1_000_000
        for trade in await self._gateway.client.fetch_my_trades(**params):
            if command.venue_order_id is not None and str(trade.get("order")) != str(
                command.venue_order_id
            ):
                continue
            instrument_id = self._instrument_id_for_symbol(str(trade["symbol"]))
            report = fill_report_from_bitpin(
                trade,
                account_id=self.account_id,
                instrument_id=instrument_id,
                ts_init=self.clock.timestamp_ns(),
            )
            self._gateway.state_store.record_fill(
                venue_fill_id=str(trade["id"]),
                venue_order_id=(
                    str(trade["order"]) if trade.get("order") is not None else None
                ),
                instrument_id=str(instrument_id),
                payload=dict(trade),
                event_time_ns=report.ts_event,
            )
            if command.start is not None and report.ts_event < command.start:
                continue
            if command.end is not None and report.ts_event > command.end:
                continue
            reports.append(report)
        return reports

    @override
    async def _generate_position_status_reports(
        self,
        command: live.GeneratePositionStatusReports,
    ) -> list[model.PositionStatusReport]:
        return []

    @override
    async def _modify_order(self, command: live.ModifyOrder) -> None:
        raise NotImplementedError("BitPin does not provide native editOrder")

    @override
    async def _cancel_all_orders(self, command: live.CancelAllOrders) -> None:
        raise NotImplementedError("BitPin does not provide native cancelAllOrders")

    @override
    async def _submit_order_list(self, command: live.SubmitOrderList) -> None:
        raise NotImplementedError(
            "Bulk order execution is not wired to the Nautilus client yet"
        )

    @override
    def _handles_order_venue(self, venue: Venue | None) -> bool:
        return venue == self.venue

    @override
    def _calculate_commission(
        self,
        instrument: object,
        last_qty: model.Quantity,
        last_px: model.Price,
        liquidity_side: LiquiditySide,
    ) -> model.Money | None:
        return None


def _native_symbol(instrument_id: InstrumentId) -> str:
    """Convert a Nautilus native BitPin instrument ID to the SDK symbol form."""

    return str(instrument_id).split(".", 1)[0].replace("_", "/", 1)
