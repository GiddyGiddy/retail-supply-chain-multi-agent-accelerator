from langgraph.graph import END, START, StateGraph

from agents.graph.nodes.allocation_sourcing import allocate_sourcing
from agents.graph.nodes.demand_inventory import assess_inventory
from agents.graph.nodes.orchestrator import (
    human_approval_hold,
    initialize_run,
    orchestrator_node,
    wrap_downstream,
)
from agents.graph.nodes.procurement_vendor import build_proposal
from agents.graph.state import SupplyChainState

builder = StateGraph(SupplyChainState)
builder.add_node("prepare_run", initialize_run)
builder.add_node("orchestrator", orchestrator_node)
builder.add_node("demand_inventory", wrap_downstream("demand_inventory", assess_inventory))
builder.add_node("allocation_sourcing", wrap_downstream("allocation_sourcing", allocate_sourcing))
builder.add_node("procurement_vendor", wrap_downstream("procurement_vendor", build_proposal))
builder.add_node("human_approval_hold", human_approval_hold)
builder.add_edge(START, "prepare_run")
builder.add_edge("prepare_run", "orchestrator")
builder.add_conditional_edges(
    "orchestrator",
    lambda state: END if state["next_node"] == "end" else state["next_node"],
    {
        "demand_inventory": "demand_inventory",
        "allocation_sourcing": "allocation_sourcing",
        "procurement_vendor": "procurement_vendor",
        "human_approval_hold": "human_approval_hold",
        END: END,
    },
)
builder.add_edge("demand_inventory", "orchestrator")
builder.add_edge("allocation_sourcing", "orchestrator")
builder.add_edge("procurement_vendor", "orchestrator")
builder.add_edge("human_approval_hold", END)
supply_chain_graph = builder.compile()


def run_supply_chain_workflow(command: str | None = None) -> SupplyChainState:
    return supply_chain_graph.invoke(
        {
            "command": command or "Create a replenishment recommendation for the supplied inventory.",
            "sku": "DEMO-COFFEE-001",
            "on_hand": 24,
            "weekly_demand": 42,
            "lead_time_days": 14,
            "warehouse_id": "DEMO-DC-01",
            "transfer_options": [
                {
                    "source_warehouse_id": "DEMO-DC-02",
                    "available_surplus": 10,
                    "unit_cost": 4.0,
                    "shipping_cost": 30.0,
                    "lead_time_days": 2,
                    "distance_miles": 100,
                }
            ],
            "vendor_options": [
                {
                    "vendor_id": "DEMO-VENDOR-A",
                    "approved": True,
                    "approved_skus": ["DEMO-COFFEE-001"],
                    "unit_cost": 3.5,
                    "delivery_cost": 15.0,
                    "lead_time_days": 7,
                    "on_time_rate": 0.95,
                }
            ],
        }
    )
