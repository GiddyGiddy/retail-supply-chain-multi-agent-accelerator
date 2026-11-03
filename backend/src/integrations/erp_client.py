class ErpClient:
    def create_purchase_order(self, *, sku: str, quantity: int) -> dict[str, str | int]:
        raise NotImplementedError(
            "Configure a provider-specific ERP adapter before enabling purchase-order writes"
        )
