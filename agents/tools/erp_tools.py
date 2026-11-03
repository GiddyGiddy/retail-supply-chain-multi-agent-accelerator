"""ERP integration tool placeholders; no external ERP writes are enabled in this starter."""


def get_safety_threshold(*, sku: str, warehouse_id: str) -> int:
    raise NotImplementedError("Connect an authenticated ERP/WMS client before reading safety thresholds")


def get_vendor_contracts(*, sku: str, warehouse_id: str) -> list[dict[str, str | int | float | bool | list[str]]]:
    raise NotImplementedError("Connect an ERP client before reading vendor contracts and SLAs")


def create_purchase_order(*, payload: dict) -> dict:
    """Create a Draft purchase order in Dynamics 365 Business Central.

    Raises NotImplementedError when BC_WRITES_ENABLED/credentials are not set, which the
    procurement node treats as "integration unavailable".
    """
    from agents.tools.business_central import BusinessCentralClient

    client = BusinessCentralClient()
    try:
        return client.create_draft_purchase_order(payload)
    finally:
        client.close()
