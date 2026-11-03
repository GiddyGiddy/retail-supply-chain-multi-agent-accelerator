class OmsClient:
    def get_inventory(self, *, sku: str) -> dict[str, str | int]:
        raise NotImplementedError(
            "Configure a provider-specific OMS adapter before reading production inventory"
        )
