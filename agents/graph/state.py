from typing import Any, TypedDict


class SupplyChainState(TypedDict, total=False):
    command: str
    sku: str
    on_hand: int
    weekly_demand: int
    lead_time_days: int
    warehouse_id: str
    safety_stock: int
    calculated_deficit: int
    inventory_events: list[dict[str, Any]]
    inventory_analysis: dict[str, Any]
    transfer_options: list[dict[str, Any]]
    vendor_options: list[dict[str, Any]]
    max_lead_time_days: float
    sourcing_weights: dict[str, float]
    sourcing_strategy: str
    selected_vendor_id: str
    selected_source_warehouse_id: str
    unit_cost: float
    cost_currency: str
    sandbox_test: bool
    total_estimated_cost: float
    sourcing_analysis: dict[str, Any]
    reorder_quantity: int
    vendor_contact_email: str
    po_approved: bool
    po_draft: dict[str, Any]
    po_payload_json: str
    po_payload_xml: str
    po_edi_850: str
    vendor_email: dict[str, str]
    escalated_review: bool
    po_lifecycle_status: str
    erp_po_number: str
    procurement_notes: str
    supplier_notes: dict[str, str]
    po_mapping_source: str
    po_mapping_overrides: list[str]
    po_mapping_warning: str
    erp_po_id: str
    erp_system: str
    erp_total_matches: bool
    message_id: str
    lifecycle_status: str
    title: str
    summary: str
    requires_human_approval: bool
    plan: list[dict[str, str]]
    plan_summary: str
    priority: str
    task_index: int
    next_node: str
    retry_count: int
    max_retries: int
    failed_node: str
    last_error: str
    status: str
    completed_tasks: list[str]
    execution_logs: list[dict[str, Any]]
    # agents/graph/state.py
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field

class AgentState(BaseModel):
    """Represents the shared memory state passed between agents in the workflow."""
    
    # Input Event Data
    sku: str = Field(..., description="The stock keeping unit identifier.")
    warehouse_id: str = Field(..., description="The origin warehouse/distribution center.")
    
    # Internal Evaluation Context (Mutated along the pipeline)
    current_stock: int = 0
    safety_threshold: int = 0
    calculated_deficit: int = 0
    
    # Sourcing decisions
    sourcing_strategy: Optional[str] = None  # "VENDOR_PO" or "INTERNAL_TRANSFER"
    selected_vendor_id: Optional[str] = None
    unit_cost: float = 0.0
    total_estimated_cost: float = 0.0
    
    # Transaction Processing Status
    po_draft_id: Optional[str] = None
    execution_status: str = "PENDING"  # PENDING, PAUSED_FOR_APPROVAL, COMPLETED, FAILED
    error_message: Optional[str] = None
    
    # Agent Execution Logs (For debugging and visibility)
    execution_logs: List[Dict[str, Any]] = Field(default_factory=list)
