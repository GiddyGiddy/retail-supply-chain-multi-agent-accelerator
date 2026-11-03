from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from agents.graph.state import SupplyChainState
from agents.tools.erp_tools import get_safety_threshold
from agents.tools.oms_tools import get_stock_balance

DEFAULT_SAFETY_DAYS = 7
SMOOTHING_ALPHA = 0.3

StockLookup = Callable[[str, str], int | None]
ThresholdLookup = Callable[[str, str], int | None]


class InventoryEvent(BaseModel):
    """A stock-change message as received from the inventory event stream."""

    sku: str = Field(min_length=1)
    warehouse_id: str = ""
    event_type: Literal["sale", "receipt", "adjustment"]
    quantity: int = Field(gt=0)
    on_hand_after: int | None = Field(default=None, ge=0)
    occurred_at: datetime | None = None


class InventorySnapshot(BaseModel):
    sku: str
    warehouse_id: str
    on_hand: int
    safety_stock: int
    daily_demand: float
    absolute_deficit: int
    on_hand_source: str
    safety_stock_source: str
    demand_source: str
    events_processed: int


def parse_inventory_events(raw_events: Iterable[dict[str, Any]], sku: str) -> list[InventoryEvent]:
    """Keep valid events for the SKU, in time order; malformed messages are skipped."""
    events: list[InventoryEvent] = []
    for raw in raw_events:
        try:
            event = InventoryEvent.model_validate(raw)
        except ValidationError:
            continue
        if event.sku == sku:
            events.append(event)
    return sorted(events, key=lambda e: e.occurred_at or datetime.min)


def forecast_daily_demand(
    daily_sales: list[float], alpha: float = SMOOTHING_ALPHA
) -> float | None:
    """Simple exponential smoothing over a daily sales series (oldest first)."""
    if not daily_sales:
        return None
    level = float(daily_sales[0])
    for value in daily_sales[1:]:
        level = alpha * value + (1 - alpha) * level
    return level


def _daily_sales_from_events(events: list[InventoryEvent]) -> list[float]:
    by_day: dict[Any, float] = {}
    for event in events:
        if event.event_type == "sale" and event.occurred_at is not None:
            day = event.occurred_at.date()
            by_day[day] = by_day.get(day, 0.0) + event.quantity
    return [by_day[day] for day in sorted(by_day)]


def calculate_deficit(on_hand: int, safety_stock: int) -> int:
    """Absolute volumetric deficit: e.g. 10 on hand, safety line 50 -> 40."""
    return max(0, safety_stock - on_hand)


def build_snapshot(
    state: SupplyChainState,
    *,
    stock_lookup: StockLookup | None = None,
    threshold_lookup: ThresholdLookup | None = None,
) -> InventorySnapshot:
    sku = state.get("sku")
    if not sku:
        raise ValueError("An SKU is required to assess inventory")
    warehouse_id = state.get("warehouse_id", "")

    events = parse_inventory_events(state.get("inventory_events", []), sku)

    on_hand, on_hand_source = None, ""
    latest_known = next((e for e in reversed(events) if e.on_hand_after is not None), None)
    if latest_known is not None:
        on_hand, on_hand_source = latest_known.on_hand_after, "event_stream"
    elif state.get("on_hand") is not None:
        on_hand, on_hand_source = state["on_hand"], "state"
    elif stock_lookup is not None:
        on_hand, on_hand_source = stock_lookup(sku, warehouse_id), "warehouse_system"
    if on_hand is None:
        raise ValueError(f"No stock balance is available for {sku}")

    daily_demand = forecast_daily_demand(_daily_sales_from_events(events))
    demand_source = "event_forecast"
    if daily_demand is None and state.get("weekly_demand") is not None:
        daily_demand, demand_source = state["weekly_demand"] / 7, "weekly_demand"
    if daily_demand is None:
        raise ValueError(f"No demand data is available for {sku}")

    safety_stock, safety_source = state.get("safety_stock"), "state"
    if safety_stock is None and threshold_lookup is not None:
        safety_stock, safety_source = threshold_lookup(sku, warehouse_id), "erp_wms"
    if safety_stock is None:
        safety_stock = round(daily_demand * DEFAULT_SAFETY_DAYS)
        safety_source = f"derived_{DEFAULT_SAFETY_DAYS}_days_of_demand"

    return InventorySnapshot(
        sku=sku,
        warehouse_id=warehouse_id,
        on_hand=on_hand,
        safety_stock=safety_stock,
        daily_demand=round(daily_demand, 2),
        absolute_deficit=calculate_deficit(on_hand, safety_stock),
        on_hand_source=on_hand_source,
        safety_stock_source=safety_source,
        demand_source=demand_source,
        events_processed=len(events),
    )


def _optional(lookup: Callable[..., int]) -> Callable[[str, str], int | None]:
    def call(sku: str, warehouse_id: str) -> int | None:
        try:
            return lookup(sku=sku, warehouse_id=warehouse_id)
        except NotImplementedError:
            return None

    return call


def assess_inventory(
    state: SupplyChainState,
    *,
    stock_lookup: StockLookup | None = None,
    threshold_lookup: ThresholdLookup | None = None,
) -> SupplyChainState:
    snapshot = build_snapshot(
        state,
        stock_lookup=stock_lookup or _optional(get_stock_balance),
        threshold_lookup=threshold_lookup or _optional(get_safety_threshold),
    )
    lead_time_days = state.get("lead_time_days", 0)
    lead_time_demand = round(snapshot.daily_demand * lead_time_days)
    return {
        "on_hand": snapshot.on_hand,
        "safety_stock": snapshot.safety_stock,
        "calculated_deficit": snapshot.absolute_deficit,
        "inventory_analysis": snapshot.model_dump(),
        "summary": (
            f"{snapshot.sku} has {snapshot.on_hand} units on hand against a safety line of "
            f"{snapshot.safety_stock} ({snapshot.safety_stock_source}), a deficit of "
            f"{snapshot.absolute_deficit} units; estimated lead-time demand is "
            f"{lead_time_demand} units."
        ),
    }
