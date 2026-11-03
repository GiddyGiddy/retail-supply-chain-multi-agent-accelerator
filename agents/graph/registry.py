import os
from typing import TypedDict


class AgentProfile(TypedDict):
    id: str
    name: str
    uses_llm: bool
    llm_mode: str  # "always", "optional" or "never"
    technology: str
    responsibility: str


AGENT_CATALOG: list[AgentProfile] = [
    {
        "id": "orchestrator",
        "name": "Orchestrator",
        "uses_llm": True,
        "llm_mode": "always",
        "technology": "LLM reasoning (planning) with deterministic fallback",
        "responsibility": "Decomposes the objective, routes tasks and recovers from node failures.",
    },
    {
        "id": "demand_inventory",
        "name": "Demand & Inventory",
        "uses_llm": False,
        "llm_mode": "never",
        "technology": "Deterministic Python (events, forecasting, WMS/ERP lookups)",
        "responsibility": "Compares stock with safety thresholds and computes the exact deficit.",
    },
    {
        "id": "allocation_sourcing",
        "name": "Allocation & Sourcing",
        "uses_llm": False,
        "llm_mode": "optional",
        "technology": "Deterministic multi-criteria optimisation; LLM only reads unstructured supplier notes",
        "responsibility": "Chooses transfer or approved vendor by cost, lead time, MOQ and reliability.",
    },
    {
        "id": "procurement_vendor",
        "name": "Procurement & Vendor",
        "uses_llm": True,
        "llm_mode": "always",
        "technology": "LLM function calling for PO mapping + deterministic Business Central HTTP client",
        "responsibility": "Maps sourcing results to the Business Central PO schema; code performs the write.",
    },
]


def agent_catalog() -> list[dict]:
    llm_configured = bool(os.getenv("OPENAI_API_KEY"))
    return [
        {
            **profile,
            "execution_mode": (
                "deterministic"
                if profile["llm_mode"] == "never"
                else "llm"
                if llm_configured
                else "deterministic_fallback"
            ),
        }
        for profile in AGENT_CATALOG
    ]
