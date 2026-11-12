import os

from agents.graph.nodes.procurement_vendor import build_po_draft
from agents.graph.state import SupplyChainState


def sandbox_proposals_enabled() -> bool:
    return os.getenv("SANDBOX_PROPOSALS_ENABLED", "false").strip().lower() in {"1", "true", "yes"}


def build_sandbox_proposal() -> SupplyChainState:
    facts: SupplyChainState = {
        "sku": "1000",
        "selected_vendor_id": "V00010",
        "sourcing_strategy": "VENDOR_PO",
        "reorder_quantity": 10,
        "unit_cost": 5.0,
        "cost_currency": "EUR",
    }
    po = build_po_draft(facts)
    return {
        **facts,
        "title": "SANDBOX TEST: Vendor V00010, item 1000",
        "summary": (
            "Sandbox test only: 10 units of item 1000 from vendor V00010 at EUR 5.00 "
            "each, total EUR 50.00 before tax. Price is an assumption, not a vendor quote. "
            "Dispatch requires a separate sandbox enablement and target verification."
        ),
        "sandbox_test": True,
        "po_draft": po.model_dump(mode="json"),
        "total_estimated_cost": po.total_amount,
        "requires_human_approval": True,
        "po_lifecycle_status": "PENDING_APPROVAL",
    }
