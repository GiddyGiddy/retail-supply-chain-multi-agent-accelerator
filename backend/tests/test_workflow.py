import json
from datetime import date, timedelta
from types import SimpleNamespace

import agents.graph.nodes.orchestrator as orchestrator
from agents.graph.nodes.allocation_sourcing import allocate_sourcing
from agents.graph.nodes.demand_inventory import assess_inventory, forecast_daily_demand
from agents.graph.nodes.procurement_vendor import CreateBusinessCentralPOInput, build_proposal
from agents.graph.registry import agent_catalog
from agents.graph.nodes.orchestrator import (
    human_approval_hold,
    orchestrator_node,
    wrap_downstream,
)
from agents.graph.workflow import run_supply_chain_workflow
from agents.guardrails.deterministic import requires_escalated_review


def test_llm_planner_uses_configured_reasoning_model(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    response_content = json.dumps(
        {
            "priority": "HIGH",
            "plan_summary": "Review stock and prepare a replenishment draft.",
            "tasks": [
                {"node": "demand_inventory", "description": "Check current inventory."},
                {"node": "allocation_sourcing", "description": "Calculate replenishment."},
                {"node": "procurement_vendor", "description": "Prepare a draft for review."},
            ],
        }
    )
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=response_content))]
            )

    class FakeOpenAI:
        def __init__(self, *, api_key: str):
            captured["api_key"] = api_key
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(orchestrator, "OpenAI", FakeOpenAI)
    plan = orchestrator._plan_with_llm("Prioritize winter replenishment.")

    assert plan.priority == "HIGH"
    assert captured["model"] == orchestrator.ORCHESTRATOR_MODEL
    assert captured["reasoning_effort"] == orchestrator.ORCHESTRATOR_REASONING_EFFORT
    assert captured["response_format"] == {"type": "json_object"}


def test_workflow_decomposes_objective_and_builds_review_proposal(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    command = "Prioritize replenishment before winter disruptions."
    result = run_supply_chain_workflow(command)

    assert result["sku"] == "DEMO-COFFEE-001"
    assert result["reorder_quantity"] > 0
    assert result["requires_human_approval"] is True
    assert "Recommend ordering" in result["summary"]
    assert result["command"] == command
    assert result["status"] == "completed"
    assert [task["node"] for task in result["plan"]] == [
        "demand_inventory",
        "allocation_sourcing",
        "procurement_vendor",
    ]
    assert len(result["completed_tasks"]) == 3
    assert any("deterministic" in log["action"] for log in result["execution_logs"])


def test_downstream_failure_retries_then_routes_to_human_review() -> None:
    def fail(_: dict) -> dict:
        raise TimeoutError("vendor lookup timed out")

    state = {
        "command": "Create a replenishment proposal.",
        "plan": [{"node": "allocation_sourcing", "description": "Choose a source."}],
        "task_index": 0,
        "retry_count": 0,
        "max_retries": 1,
        "execution_logs": [],
    }
    failed = wrap_downstream("allocation_sourcing", fail)(state)
    retry = orchestrator_node({**state, **failed})

    assert retry["next_node"] == "allocation_sourcing"
    assert retry["retry_count"] == 1
    assert "vendor lookup timed out" in failed["last_error"]

    failed_again = wrap_downstream("allocation_sourcing", fail)({**state, **retry, **failed})
    held = orchestrator_node({**state, **retry, **failed_again})
    review = human_approval_hold({**state, **retry, **failed_again, **held})

    assert held["next_node"] == "human_approval_hold"
    assert held["status"] == "needs_human_review"
    assert "vendor lookup timed out" in review["summary"]
    assert review["requires_human_approval"] is True


def test_deficit_is_safety_line_minus_on_hand() -> None:
    result = assess_inventory(
        {"sku": "SKU-1", "on_hand": 10, "safety_stock": 50, "weekly_demand": 14, "lead_time_days": 7}
    )

    assert result["calculated_deficit"] == 40
    assert result["inventory_analysis"]["safety_stock_source"] == "state"


def test_event_stream_overrides_state_and_feeds_forecast() -> None:
    events = [
        {"sku": "SKU-1", "event_type": "sale", "quantity": 10, "occurred_at": "2026-01-01T10:00:00"},
        {"sku": "SKU-1", "event_type": "sale", "quantity": 30, "occurred_at": "2026-01-02T10:00:00",
         "on_hand_after": 12},
        {"sku": "OTHER", "event_type": "sale", "quantity": 99, "occurred_at": "2026-01-02T11:00:00"},
        {"bad": "message"},
    ]
    result = assess_inventory(
        {"sku": "SKU-1", "on_hand": 500, "safety_stock": 50, "inventory_events": events, "lead_time_days": 5}
    )

    analysis = result["inventory_analysis"]
    assert result["on_hand"] == 12 and analysis["on_hand_source"] == "event_stream"
    assert analysis["events_processed"] == 2
    assert analysis["demand_source"] == "event_forecast"
    assert analysis["daily_demand"] == round(forecast_daily_demand([10, 30]), 2)
    assert result["calculated_deficit"] == 38


def test_missing_sku_or_stock_data_raises() -> None:
    for state in ({"on_hand": 1, "weekly_demand": 7}, {"sku": "SKU-1", "weekly_demand": 7}):
        try:
            assess_inventory(state)
        except ValueError:
            continue
        raise AssertionError("expected ValueError")


def _vendor(vendor_id: str, **overrides) -> dict:
    return {
        "vendor_id": vendor_id,
        "approved": True,
        "approved_skus": ["SKU-1"],
        "unit_cost": 5.0,
        "delivery_cost": 0.0,
        "lead_time_days": 10,
        "on_time_rate": 0.9,
        **overrides,
    }


def test_nearby_transfer_beats_vendor_when_cheaper_and_faster() -> None:
    state = {
        "sku": "SKU-1",
        "calculated_deficit": 40,
        "transfer_options": [
            {"source_warehouse_id": "DC-2", "available_surplus": 100, "unit_cost": 4.0,
             "shipping_cost": 20.0, "lead_time_days": 1, "distance_miles": 100}
        ],
        "vendor_options": [_vendor("V1")],
    }
    result = allocate_sourcing(state)

    assert result["sourcing_strategy"] == "INTERNAL_TRANSFER"
    assert result["selected_source_warehouse_id"] == "DC-2"
    assert result["reorder_quantity"] == 40
    assert result["total_estimated_cost"] == 180.0


def test_transfer_that_cannot_cover_deficit_falls_back_to_ranked_approved_vendor() -> None:
    state = {
        "sku": "SKU-1",
        "calculated_deficit": 40,
        "transfer_options": [
            {"source_warehouse_id": "DC-2", "available_surplus": 10, "unit_cost": 1.0, "lead_time_days": 1}
        ],
        "vendor_options": [
            _vendor("UNAPPROVED", approved=False, unit_cost=1.0, lead_time_days=1),
            _vendor("WRONG-SKU", approved_skus=["OTHER"], unit_cost=1.0, lead_time_days=1),
            _vendor("SLOW-CHEAP", unit_cost=4.0, lead_time_days=20, on_time_rate=0.8),
            _vendor("BALANCED", unit_cost=3.0, lead_time_days=5, on_time_rate=0.97, min_order_quantity=50),
        ],
    }
    result = allocate_sourcing(state)

    assert result["sourcing_strategy"] == "VENDOR_PO"
    assert result["selected_vendor_id"] == "BALANCED"
    assert result["reorder_quantity"] == 50
    ranked_ids = [c["source_id"] for c in result["sourcing_analysis"]["ranking"]]
    assert set(ranked_ids) == {"SLOW-CHEAP", "BALANCED"}


def test_sourcing_raises_when_nothing_eligible_and_skips_zero_deficit() -> None:
    try:
        allocate_sourcing({"sku": "SKU-1", "calculated_deficit": 5, "vendor_options": [_vendor("V", approved=False)]})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")

    assert allocate_sourcing({"sku": "SKU-1", "calculated_deficit": 0})["reorder_quantity"] == 0


def test_procurement_builds_po_payloads_and_email_without_external_writes() -> None:
    state = {
        "sku": "SKU-1", "warehouse_id": "DC-1", "sourcing_strategy": "VENDOR_PO",
        "selected_vendor_id": "V<1>&", "reorder_quantity": 40, "unit_cost": 5.0,
        "summary": "Deficit.", "vendor_contact_email": "sales@vendor.example",
    }
    result = build_proposal(state)

    assert result["po_lifecycle_status"] == "PENDING_APPROVAL"
    assert result["requires_human_approval"] is True
    assert json.loads(result["po_payload_json"])["total_amount"] == 200.0
    assert "V&lt;1&gt;&amp;" in result["po_payload_xml"]
    assert result["po_edi_850"].startswith("ST*850")
    assert result["vendor_email"]["to"] == "sales@vendor.example"
    assert "erp_po_number" not in result


def test_procurement_never_dispatches_or_notifies_even_if_state_says_approved(monkeypatch) -> None:
    from agents.tools import erp_tools, oms_tools

    def forbidden(*args, **kwargs):
        raise AssertionError("Proposal generation must not perform external writes")

    monkeypatch.setattr(erp_tools, "create_purchase_order", forbidden)
    monkeypatch.setattr(oms_tools, "send_vendor_message", forbidden)
    monkeypatch.setattr(oms_tools, "check_po_acknowledgment", forbidden)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    state = {
        "sku": "SKU-1", "sourcing_strategy": "VENDOR_PO", "selected_vendor_id": "V1",
        "reorder_quantity": 10, "unit_cost": 2.0, "summary": "x",
        "vendor_contact_email": "a@b.example",
    }
    for approved in (False, True):
        result = build_proposal({**state, "po_approved": approved})
        assert result["po_lifecycle_status"] == "PENDING_APPROVAL"
        assert result["requires_human_approval"] is True
        assert result["vendor_email"]["to"] == "a@b.example"
        assert "erp_po_number" not in result
        assert "message_id" not in result
    escalated = build_proposal({**state, "po_approved": True, "reorder_quantity": 100_000})
    assert escalated["escalated_review"] is True


def _po_state(**overrides) -> dict:
    return {
        "sku": "SKU-1", "warehouse_id": "DC-1", "sourcing_strategy": "VENDOR_PO",
        "selected_vendor_id": "V1", "reorder_quantity": 10, "unit_cost": 2.0, "summary": "x",
        **overrides,
    }


def test_llm_mapping_can_refine_receipt_date_but_not_sourcing_facts(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    future = (date.today() + timedelta(days=30)).isoformat()
    good = lambda state, baseline: CreateBusinessCentralPOInput(
        vendor_number="V1", item_number="SKU-1", quantity=10, unit_cost=2.0,
        location_code="DC-2", requested_receipt_date=future, currency_code="USD",
    )
    result = build_proposal(_po_state(procurement_notes="deliver to DC-2"), mapper=good)
    assert result["po_mapping_source"] == "llm"
    assert result["po_mapping_overrides"] == ["requested_delivery_date", "ship_to_warehouse_id"]
    assert result["po_draft"]["ship_to_warehouse_id"] == "DC-2"

    tampered = lambda state, baseline: CreateBusinessCentralPOInput(
        vendor_number="V1", item_number="SKU-1", quantity=1000, unit_cost=2.0
    )
    rejected = build_proposal(_po_state(), mapper=tampered)
    assert rejected["po_mapping_source"] == "deterministic"
    assert "rejected" in rejected["po_mapping_warning"]
    assert rejected["po_draft"]["lines"][0]["quantity"] == 10

    broken = build_proposal(_po_state(), mapper=lambda s, b: (_ for _ in ()).throw(ValueError("bad")))
    assert broken["po_mapping_source"] == "deterministic"


def test_procurement_uses_sourcing_currency_and_rejects_llm_currency_change(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    state = _po_state(cost_currency="EUR")
    result = build_proposal(state)
    assert result["po_draft"]["currency"] == "EUR"
    tampered = lambda state, baseline: CreateBusinessCentralPOInput(
        vendor_number="V1", item_number="SKU-1", quantity=10, unit_cost=2.0, currency_code="USD",
    )
    result = build_proposal(state, mapper=tampered)
    assert result["po_mapping_source"] == "deterministic"
    assert "rejected" in result["po_mapping_warning"]
    assert result["po_draft"]["currency"] == "EUR"
    assert build_proposal(_po_state())["po_draft"]["currency"] == "USD"


def test_supplier_notes_can_only_lower_reliability_within_bounds() -> None:
    state = {
        "sku": "SKU-1", "calculated_deficit": 10,
        "vendor_options": [
            _vendor("STRIKE", unit_cost=5.0, lead_time_days=5, on_time_rate=0.99),
            _vendor("STEADY", unit_cost=5.0, lead_time_days=5, on_time_rate=0.95),
        ],
        "supplier_notes": {"STRIKE": "Port strike announced"},
    }
    result = allocate_sourcing(state, risk_interpreter=lambda notes: {"STRIKE": 5.0, "GHOST": 0.3, "STEADY": -1})
    assert result["selected_vendor_id"] == "STEADY"
    assert result["sourcing_analysis"]["risk_adjustments"] == {"STRIKE": 0.3}

    failing = allocate_sourcing(state, risk_interpreter=lambda notes: (_ for _ in ()).throw(ValueError("x")))
    assert failing["selected_vendor_id"] == "STRIKE"
    assert "risk_warning" in failing["sourcing_analysis"]


def test_agent_catalog_marks_which_agents_use_llm(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    catalog = {agent["id"]: agent for agent in agent_catalog()}
    assert catalog["demand_inventory"]["uses_llm"] is False
    assert catalog["demand_inventory"]["execution_mode"] == "deterministic"
    assert catalog["allocation_sourcing"]["llm_mode"] == "optional"
    assert catalog["procurement_vendor"]["execution_mode"] == "deterministic_fallback"
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    assert {a["id"]: a for a in agent_catalog()}["procurement_vendor"]["execution_mode"] == "llm"


def test_large_order_value_requires_escalated_review() -> None:
    assert requires_escalated_review(49_999) is False
    assert requires_escalated_review(50_000) is True
