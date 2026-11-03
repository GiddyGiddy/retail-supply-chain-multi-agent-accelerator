import json

import httpx
import pytest

from agents.tools.business_central import (
    BusinessCentralClient,
    BusinessCentralConfig,
    BusinessCentralError,
)

TENANT = "11111111-1111-1111-1111-111111111111"
COMPANY = "22222222-2222-2222-2222-222222222222"
SECRET = "super-secret-value"

PAYLOAD = {
    "po_reference": "DRAFT-V1-SKU-1-20260101",
    "vendor_id": "V1",
    "ship_to_warehouse_id": "DC1",
    "currency": "EUR",
    "requested_delivery_date": "2026-02-01",
    "lines": [{"sku": "SKU-1", "quantity": 10, "unit_price": 2.5}],
}


def _config(**overrides) -> BusinessCentralConfig:
    values = dict(
        tenant_id=TENANT, client_id="app", client_secret=SECRET, company_id=COMPANY, writes_enabled=True
    )
    return BusinessCentralConfig(**{**values, **overrides})


def _client(handler) -> BusinessCentralClient:
    return BusinessCentralClient(_config(), transport=httpx.MockTransport(handler), sleep=lambda _: None)


class FakeBC:
    def __init__(self, vendor=None, existing=None, fail_lines=False, total=25.0) -> None:
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[dict] = []
        self.vendor = {"number": "V1", "blocked": " "} if vendor is None else vendor
        self.existing = existing or []
        self.fail_lines = fail_lines
        self.total = total

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "oauth2" in path:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer tok"
        self.calls.append((request.method, path.split("/companies(")[1].split(")", 1)[1]))
        if request.content:
            self.bodies.append(json.loads(request.content))
        if request.method == "GET" and path.endswith("/vendors"):
            return httpx.Response(200, json={"value": [self.vendor] if self.vendor else []})
        if request.method == "GET" and path.endswith("/purchaseOrders"):
            return httpx.Response(200, json={"value": self.existing})
        if request.method == "POST" and path.endswith("/purchaseOrders"):
            return httpx.Response(201, json={"id": "po-guid", "@odata.etag": 'W/"1"'})
        if request.method == "POST" and path.endswith("/purchaseOrderLines"):
            if self.fail_lines and self.bodies[-1].get("lineType") == "Item":
                return httpx.Response(400, json={"error": {"code": "Bad", "message": "Item does not exist"}})
            return httpx.Response(201, json={})
        if request.method == "DELETE":
            assert request.headers["If-Match"] == 'W/"1"'
            return httpx.Response(204)
        return httpx.Response(
            200, json={"number": "106001", "status": "Draft", "totalAmountExcludingTax": self.total}
        )


def test_creates_draft_po_with_header_comment_and_item_lines() -> None:
    bc = FakeBC()
    result = _client(bc).create_draft_purchase_order(PAYLOAD)

    assert result["po_number"] == "106001"
    assert result["erp_status"] == "Draft" and result["total_matches"] is True
    header = bc.bodies[0]
    assert header == {"vendorNumber": "V1", "requestedReceiptDate": "2026-02-01", "currencyCode": "EUR"}
    assert bc.bodies[1] == {"lineType": "Comment", "description": PAYLOAD["po_reference"]}
    assert bc.bodies[2]["lineObjectNumber"] == "SKU-1" and bc.bodies[2]["locationCode"] == "DC1"
    assert not any(method == "DELETE" for method, _ in bc.calls)


def test_existing_draft_with_same_reference_is_not_duplicated() -> None:
    existing = [
        {
            "id": "old", "number": "106000", "status": "Draft",
            "purchaseOrderLines": [{"lineType": "Comment", "description": PAYLOAD["po_reference"]}],
        }
    ]
    bc = FakeBC(existing=existing)
    result = _client(bc).create_draft_purchase_order(PAYLOAD)

    assert result["po_number"] == "106000" and result["duplicate"] is True
    assert ("POST", "/purchaseOrders") not in bc.calls


def test_blocked_or_missing_vendor_is_rejected_before_any_write() -> None:
    for vendor in ({"number": "V1", "blocked": "All"}, None):
        bc = FakeBC(vendor=vendor)
        if vendor is None:
            bc.vendor = None
        with pytest.raises(ValueError):
            _client(bc).create_draft_purchase_order(PAYLOAD)
        assert not any(method == "POST" and path == "/purchaseOrders" for method, path in bc.calls)


def test_line_failure_rolls_back_draft_and_error_has_no_secrets() -> None:
    bc = FakeBC(fail_lines=True)
    with pytest.raises(BusinessCentralError) as error:
        _client(bc).create_draft_purchase_order(PAYLOAD)

    assert ("DELETE", "/purchaseOrders(po-guid)") in bc.calls
    assert SECRET not in str(error.value) and "tok" not in str(error.value).split()


def test_total_mismatch_is_reported() -> None:
    result = _client(FakeBC(total=99.0)).create_draft_purchase_order(PAYLOAD)
    assert result["total_matches"] is False


def test_rejects_injection_in_codes_and_disabled_config() -> None:
    with pytest.raises(ValueError):
        _client(FakeBC()).create_draft_purchase_order({**PAYLOAD, "vendor_id": "V1' or 1 eq 1"})
    with pytest.raises(NotImplementedError):
        BusinessCentralClient(_config(writes_enabled=False))
    with pytest.raises(NotImplementedError):
        BusinessCentralClient(_config(client_secret=""))
