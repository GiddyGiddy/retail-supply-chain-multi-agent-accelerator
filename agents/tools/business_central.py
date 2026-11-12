"""Dynamics 365 Business Central (API v2.0) adapter for draft purchase-order writes.

Security model:
- OAuth2 client-credentials against Microsoft Entra ID; secrets come only from the environment.
- Writes are off unless BC_WRITES_ENABLED=true, and the adapter only ever creates Draft POs.
  It never releases, posts or deletes anything except a draft it created itself (rollback).
- Requests go only to the Business Central API host, without following redirects.
- Identifiers sent to OData are validated against Business Central's Code[] character rules.
- Error messages are sanitised so tokens and secrets never reach logs or graph state.
"""

import os
import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable
from uuid import UUID

import httpx

API_HOST = "https://api.businesscentral.dynamics.com"
TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
SCOPE = f"{API_HOST}/.default"

_CODE = re.compile(r"^[A-Za-z0-9._\-/ ]{1,20}$")
_GUID = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
_ENV = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
_RETRY_STATUSES = {429, 502, 503, 504}


class BusinessCentralError(RuntimeError):
    """Raised with a sanitised message; never includes credentials or raw headers."""


@dataclass(frozen=True)
class BusinessCentralConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    company_id: str
    environment: str = "production"
    local_currency: str = "USD"
    writes_enabled: bool = False

    @classmethod
    def from_env(cls) -> "BusinessCentralConfig":
        env = os.environ
        return cls(
            tenant_id=env.get("BC_TENANT_ID", ""),
            client_id=env.get("BC_CLIENT_ID", ""),
            client_secret=env.get("BC_CLIENT_SECRET", ""),
            company_id=env.get("BC_COMPANY_ID", ""),
            environment=env.get("BC_ENVIRONMENT", "production"),
            local_currency=env.get("BC_LOCAL_CURRENCY", "USD"),
            writes_enabled=env.get("BC_WRITES_ENABLED", "false").strip().lower() in {"1", "true", "yes"},
        )

    def validate(self) -> None:
        if not self.writes_enabled:
            raise NotImplementedError("Business Central writes are disabled; set BC_WRITES_ENABLED=true to enable")
        missing = [
            name
            for name, value in (
                ("BC_TENANT_ID", self.tenant_id),
                ("BC_CLIENT_ID", self.client_id),
                ("BC_CLIENT_SECRET", self.client_secret),
                ("BC_COMPANY_ID", self.company_id),
            )
            if not value
        ]
        if missing:
            raise NotImplementedError(f"Business Central is not configured: missing {', '.join(missing)}")
        if not (_GUID.match(self.tenant_id) and _GUID.match(self.company_id) and _ENV.match(self.environment)):
            raise NotImplementedError("Business Central tenant, company id or environment has an invalid format")

    @property
    def company_url(self) -> str:
        return f"{API_HOST}/v2.0/{self.tenant_id}/{self.environment}/api/v2.0/companies({self.company_id})"


def _code(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not _CODE.match(text):
        raise ValueError(f"{label} '{text[:25]}' is not a valid Business Central code")
    return text


class BusinessCentralClient:
    def __init__(
        self,
        config: BusinessCentralConfig | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 15.0,
        sleep: Any = time.sleep,
    ) -> None:
        self.config = config or BusinessCentralConfig.from_env()
        self.config.validate()
        self._http = httpx.Client(transport=transport, timeout=timeout, follow_redirects=False)
        self._sleep = sleep
        self._token: str | None = None
        self._token_expiry = 0.0

    def close(self) -> None:
        self._http.close()

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        response = self._http.post(
            TOKEN_URL.format(tenant=self.config.tenant_id),
            data={
                "grant_type": "client_credentials",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "scope": SCOPE,
            },
        )
        if response.status_code != 200:
            raise BusinessCentralError(f"Entra token request failed with HTTP {response.status_code}")
        body = response.json()
        self._token = body["access_token"]
        self._token_expiry = time.time() + int(body.get("expires_in", 300))
        return self._token

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        etag: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._access_token()}", "Accept": "application/json"}
        if etag:
            headers["If-Match"] = etag
        # Only reads are retried; a blind retry of a POST could create a duplicate PO.
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            response = self._http.request(
                method, f"{self.config.company_url}/{path}", json=json, params=params, headers=headers
            )
            if response.status_code in _RETRY_STATUSES and attempt < attempts - 1:
                self._sleep(min(float(response.headers.get("Retry-After", 1) or 1), 5.0))
                continue
            break
        if response.status_code >= 400:
            detail = ""
            try:
                error = response.json().get("error", {})
                detail = f" {error.get('code', '')}: {str(error.get('message', ''))[:200]}"
            except ValueError:
                pass
            raise BusinessCentralError(f"Business Central {method} {path.split('?')[0]} failed with HTTP {response.status_code}.{detail}")
        return response.json() if response.content else {}

    def get_vendor(self, number: str) -> dict[str, Any]:
        number = _code(number, "vendor number")
        rows = self._request("GET", "vendors", params={"$filter": f"number eq '{number}'", "$top": "1"})
        vendors = rows.get("value", [])
        if not vendors:
            raise ValueError(f"Vendor '{number}' does not exist in Business Central")
        if vendors[0].get("blocked") not in (None, "", " ", "_x0020_"):
            raise ValueError(f"Vendor '{number}' is blocked in Business Central")
        return vendors[0]

    def find_draft_by_reference(self, vendor_number: str, reference: str) -> dict[str, Any] | None:
        """Idempotency check: the agent's PO reference is stored in a comment line."""
        vendor_number = _code(vendor_number, "vendor number")
        rows = self._request(
            "GET",
            "purchaseOrders",
            params={
                "$filter": f"vendorNumber eq '{vendor_number}'",
                "$expand": "purchaseOrderLines($select=lineType,description)",
                "$select": "id,number,status",
                "$top": "100",
            },
        )
        for order in rows.get("value", []):
            if order.get("status") != "Draft":
                continue
            if any(
                line.get("lineType") == "Comment" and line.get("description") == reference
                for line in order.get("purchaseOrderLines", [])
            ):
                return order
        return None

    def complete_sandbox_draft(self, order_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Complete one verified comment-only draft; never create or delete a header."""
        try:
            order_id = str(UUID(order_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise BusinessCentralError("Invalid Business Central order ID") from error
        path = f"purchaseOrders({order_id})"

        def snapshot() -> tuple[dict[str, Any], list[dict[str, Any]]]:
            order = self._request("GET", path)
            lines = self._request("GET", f"{path}/purchaseOrderLines")
            rows = lines.get("value")
            if not isinstance(rows, list) or lines.get("@odata.nextLink") or not all(isinstance(x, dict) for x in rows):
                raise BusinessCentralError("Draft lines could not be fully verified")
            currency = order.get("currencyCode")
            if (
                not isinstance(order.get("id"), str) or order["id"].lower() != order_id
                or not order.get("number")
                or order.get("status") != "Draft"
                or order.get("vendorNumber") != payload["vendor_id"]
                or not isinstance(currency, str) or (currency or self.config.local_currency) != payload["currency"]
            ):
                raise BusinessCentralError("Draft header does not match the approved sandbox order")
            return order, rows

        def money(value: Any) -> Decimal:
            try:
                result = Decimal(str(value))
                if not result.is_finite():
                    raise ValueError("Non-finite amount")
                return result
            except (InvalidOperation, ValueError, TypeError) as error:
                raise BusinessCentralError("Draft contains an invalid or missing amount") from error

        order, rows = snapshot()
        if (
            len(rows) != 1
            or rows[0].get("lineType") != "Comment"
            or rows[0].get("description") != payload["po_reference"]
            or money(order.get("totalAmountExcludingTax")) != 0
        ):
            raise BusinessCentralError("Draft is not the expected comment-only order; manual review required")
        line = payload["lines"][0]
        expected = money(line["quantity"]) * money(line["unit_price"])
        self._request("POST", f"{path}/purchaseOrderLines", json={
            "lineType": "Item",
            "lineObjectNumber": _code(line["sku"], "item number"),
            "quantity": line["quantity"],
            "directUnitCost": line["unit_price"],
        })
        final, rows = snapshot()
        comments = [x for x in rows if x.get("lineType") == "Comment"]
        items = [x for x in rows if x.get("lineType") == "Item"]
        empty_id = "00000000-0000-0000-0000-000000000000"
        if (
            final.get("number") != order["number"]
            or len(rows) != 2 or len(comments) != 1 or len(items) != 1
            or comments[0].get("description") != payload["po_reference"]
            or items[0].get("lineObjectNumber") != line["sku"]
            or money(items[0].get("quantity")) != money(line["quantity"])
            or money(items[0].get("directUnitCost")) != money(line["unit_price"])
            or money(items[0].get("discountAmount")) != 0
            or money(items[0].get("discountPercent")) != 0
            or "locationId" not in items[0]
            or items[0]["locationId"] not in (None, "", empty_id)
            or money(items[0].get("amountExcludingTax")) != expected
            or money(final.get("totalAmountExcludingTax")) != expected
        ):
            raise BusinessCentralError("Completed draft does not match the approved order; manual review required")
        return self._result(final["number"], order_id, final["status"], None)

    def create_draft_purchase_order(
        self, payload: dict[str, Any], *,
        on_order_created: Callable[[str], None] | None = None,
        reject_existing: bool = False,
    ) -> dict[str, Any]:
        vendor_number = _code(payload.get("vendor_id"), "vendor number")
        reference = str(payload.get("po_reference", ""))[:100]
        if not reference:
            raise ValueError("po_reference is required")
        lines = payload.get("lines") or []
        if not lines:
            raise ValueError("A purchase order needs at least one line")

        self.get_vendor(vendor_number)
        existing = self.find_draft_by_reference(vendor_number, reference)
        if existing:
            if reject_existing:
                raise BusinessCentralError("A draft with this reference already exists; manual review required")
            return self._result(existing["number"], existing["id"], "Draft", None, duplicate=True)

        body: dict[str, Any] = {"vendorNumber": vendor_number}
        if payload.get("requested_delivery_date"):
            body["requestedReceiptDate"] = payload["requested_delivery_date"]
        currency = str(payload.get("currency") or "")
        if currency and currency != self.config.local_currency:
            body["currencyCode"] = currency
        location = payload.get("ship_to_warehouse_id")
        location_code = _code(location, "location code") if location else None

        order = self._request("POST", "purchaseOrders", json=body)
        order_id, etag = order["id"], order.get("@odata.etag")
        if on_order_created is not None:
            on_order_created(order_id)
        try:
            order_currency = self._request(
                "GET", f"purchaseOrders({order_id})", params={"$select": "currencyCode"},
            )
            actual_currency = order_currency.get("currencyCode")
            if not isinstance(actual_currency, str) or (
                actual_currency or self.config.local_currency
            ) != currency:
                raise BusinessCentralError("Business Central draft currency does not match the approved currency")
            self._request(
                "POST",
                f"purchaseOrders({order_id})/purchaseOrderLines",
                json={"lineType": "Comment", "description": reference},
            )
            expected = 0.0
            for line in lines:
                quantity, cost = int(line["quantity"]), float(line["unit_price"])
                if quantity <= 0 or cost < 0:
                    raise ValueError("Line quantity must be positive and unit price non-negative")
                line_body: dict[str, Any] = {
                    "lineType": "Item",
                    "lineObjectNumber": _code(line["sku"], "item number"),
                    "quantity": quantity,
                    "directUnitCost": cost,
                }
                if location_code:
                    line_body["locationCode"] = location_code
                self._request("POST", f"purchaseOrders({order_id})/purchaseOrderLines", json=line_body)
                expected += quantity * cost
        except Exception:
            if on_order_created is not None:
                # The durable dispatcher retains the known order ID for manual reconciliation.
                raise
            # Remove the half-built draft so retries start clean.
            try:
                self._request("DELETE", f"purchaseOrders({order_id})", etag=etag)
            except BusinessCentralError:
                pass
            raise

        created = self._request(
            "GET", f"purchaseOrders({order_id})", params={"$select": "number,status,totalAmountExcludingTax"}
        )
        return self._result(
            created["number"], order_id, created.get("status", "Draft"), (created.get("totalAmountExcludingTax"), expected)
        )

    @staticmethod
    def _result(
        number: str, order_id: str, status: str, totals: tuple[Any, float] | None, duplicate: bool = False
    ) -> dict[str, Any]:
        matches = True if totals is None else abs(float(totals[0] or 0) - totals[1]) < 0.01
        return {
            "po_number": number,
            "erp_po_id": order_id,
            "erp_system": "BusinessCentral",
            "erp_status": status,
            "total_matches": matches,
            "duplicate": duplicate,
        }
