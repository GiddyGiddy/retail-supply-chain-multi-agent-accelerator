"""OMS integration tool placeholders."""


def get_stock_balance(*, sku: str, warehouse_id: str) -> int:
    raise NotImplementedError("Connect a WMS/OMS client before reading real-time stock balances")


def get_transfer_options(*, sku: str, warehouse_id: str) -> list[dict[str, str | int | float]]:
    raise NotImplementedError("Connect a WMS/logistics client before reading inter-warehouse transfer options")


def send_vendor_message(*, to: str, subject: str, body: str) -> dict[str, str]:
    raise NotImplementedError("Connect an approved email/EDI/supplier-portal gateway before contacting vendors")


def check_po_acknowledgment(*, po_reference: str) -> dict[str, str | bool]:
    raise NotImplementedError("Connect the vendor acknowledgment feed (EDI 855 / portal / mailbox) first")


def get_recent_sales(*, sku: str) -> dict[str, str]:
    raise NotImplementedError("Connect an OMS client before querying production order data")
