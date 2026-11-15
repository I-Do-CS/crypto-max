from __future__ import annotations

import httpx
from bitpin_sdk import Bitpin, Config
from bitpin_sdk.errors import RequestTimeout


def _order(order_id: int, identifier: str) -> dict[str, object]:
    return {
        "id": order_id,
        "symbol": "BTC_IRT",
        "type": "limit",
        "side": "buy",
        "price": "17000000000",
        "average_price": "0",
        "stop_price": None,
        "oco_target_price": None,
        "base_amount": "0.001",
        "quote_amount": "17000000",
        "identifier": identifier,
        "state": "active",
        "closed_at": None,
        "created_at": "2026-11-15T10:00:00+00:00",
        "dealed_base_amount": "0",
        "dealed_quote_amount": "0",
        "req_to_cancel": False,
        "commission": "0",
    }


def test_id_cursor_pagination_uses_lowest_id_as_offset() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/authenticate/"):
            return httpx.Response(
                200,
                json={"access": "access-token", "refresh": "refresh-token"},
                request=request,
            )
        if request.url.path.endswith("/odr/orders/"):
            offset = request.url.params.get("offset")
            if offset is None:
                body = [_order(200, "first")]
            elif offset == "200":
                body = [_order(100, "second")]
            else:
                body = []
            return httpx.Response(200, json=body, request=request)
        return httpx.Response(404, json={"detail": "unexpected"}, request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http,
        Bitpin(
            Config(api_key="api", secret_key="secret"),
            http_client=http,
        ) as client,
    ):
        orders = client.fetch_open_orders(
            limit=1,
            params={"paginate": True},
        )

    order_requests = [
        request for request in requests if request.url.path.endswith("/odr/orders/")
    ]
    assert [request.url.params.get("offset") for request in order_requests] == [
        None,
        "200",
        "100",
    ]
    assert [order["id"] for order in orders] == ["100", "200"]


def test_create_order_transport_timeout_is_not_retried() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/authenticate/"):
            return httpx.Response(
                200,
                json={"access": "access-token", "refresh": "refresh-token"},
                request=request,
            )
        raise httpx.ReadTimeout("simulated create timeout", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http,
        Bitpin(
            Config(api_key="api", secret_key="secret"),
            http_client=http,
        ) as client,
    ):
        try:
            client.create_order(
                "BTC/IRT",
                "limit",
                "buy",
                "0.001",
                "17000000000",
                {"clientOrderId": "timeout-1"},
            )
        except RequestTimeout:
            pass
        else:
            raise AssertionError("create_order unexpectedly succeeded")

    order_requests = [
        request for request in requests if request.url.path.endswith("/odr/orders/")
    ]
    assert len(order_requests) == 1
