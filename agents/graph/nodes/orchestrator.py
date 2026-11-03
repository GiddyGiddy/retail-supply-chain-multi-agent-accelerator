import json
import logging
import os
from collections.abc import Callable
from typing import Any, Literal

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, Field, ValidationError

from agents.graph.state import SupplyChainState

logger = logging.getLogger(__name__)

ROUTING_ORDER = ("demand_inventory", "allocation_sourcing", "procurement_vendor")
ROUTING_MATRIX = {
    "demand_inventory": {"success": "allocation_sourcing", "failure": "human_approval_hold"},
    "allocation_sourcing": {"success": "procurement_vendor", "failure": "human_approval_hold"},
    "procurement_vendor": {"success": "end", "failure": "human_approval_hold"},
}
MAX_RETRIES = 2
DEFAULT_COMMAND = "Create a replenishment recommendation for the supplied inventory."
ORCHESTRATOR_MODEL = os.getenv("ORCHESTRATOR_MODEL", "o3")
ORCHESTRATOR_REASONING_EFFORT = os.getenv("ORCHESTRATOR_REASONING_EFFORT", "high")

ORCHESTRATOR_SYSTEM_PROMPT = """
You supervise a retail supply-chain workflow. Decompose the user's business objective
into concise, sequential operational tasks for the available agents. Analyze privately;
never output hidden chain-of-thought. Return only a JSON object with:
{
  "priority": "LOW" | "MEDIUM" | "HIGH",
  "plan_summary": "brief decision summary without private reasoning",
  "tasks": [
    {"node": "demand_inventory", "description": "..."},
    {"node": "allocation_sourcing", "description": "..."},
    {"node": "procurement_vendor", "description": "..."}
  ]
}
Keep all three tasks in that exact order so inventory checks precede sourcing and
procurement. Do not invent inventory, vendor, warehouse, cost, or customer data.
Proposals are for human review; do not authorize ERP writes.
"""


class PlannedTask(BaseModel):
    node: Literal["demand_inventory", "allocation_sourcing", "procurement_vendor"]
    description: str = Field(min_length=1, max_length=500)


class SupervisorPlan(BaseModel):
    priority: Literal["LOW", "MEDIUM", "HIGH"]
    plan_summary: str = Field(min_length=1, max_length=500)
    tasks: list[PlannedTask] = Field(min_length=3, max_length=3)


def _fallback_plan(command: str) -> SupervisorPlan:
    return SupervisorPlan(
        priority="MEDIUM",
        plan_summary="Run the supported inventory-to-procurement review sequence.",
        tasks=[
            PlannedTask(
                node="demand_inventory",
                description=f"Assess inventory relevant to this objective: {command}",
            ),
            PlannedTask(
                node="allocation_sourcing",
                description="Calculate the replenishment quantity using the available inventory state.",
            ),
            PlannedTask(
                node="procurement_vendor",
                description="Build a draft proposal for human review; do not submit a purchase order.",
            ),
        ],
    )


def _plan_with_llm(command: str) -> SupervisorPlan:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return _fallback_plan(command)

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=ORCHESTRATOR_MODEL,
        reasoning_effort=ORCHESTRATOR_REASONING_EFFORT,
        max_completion_tokens=1200,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": ORCHESTRATOR_SYSTEM_PROMPT},
            {"role": "user", "content": command},
        ],
    )
    content = response.choices[0].message.content
    plan = SupervisorPlan.model_validate(json.loads(content or "{}"))
    if tuple(task.node for task in plan.tasks) != ROUTING_ORDER:
        raise ValueError("The supervisor plan must follow the supported routing order")
    return plan


def initialize_run(state: SupplyChainState) -> SupplyChainState:
    return {
        "command": state.get("command") or DEFAULT_COMMAND,
        "task_index": state.get("task_index", 0),
        "retry_count": state.get("retry_count", 0),
        "max_retries": state.get("max_retries", MAX_RETRIES),
        "completed_tasks": state.get("completed_tasks", []),
        "execution_logs": state.get("execution_logs", []),
    }


def orchestrator_node(state: SupplyChainState) -> SupplyChainState:
    logs = list(state.get("execution_logs", []))
    failed_node = state.get("failed_node")

    if failed_node:
        retry_count = state.get("retry_count", 0)
        if retry_count < state.get("max_retries", MAX_RETRIES):
            logs.append(
                {
                    "agent": "Orchestrator",
                    "action": f"Retrying {failed_node} after error: {state.get('last_error', 'unknown error')}",
                }
            )
            return {
                "next_node": failed_node,
                "retry_count": retry_count + 1,
                "execution_logs": logs,
            }
        logs.append(
            {
                "agent": "Orchestrator",
                "action": f"Retry limit reached for {failed_node}; routing to human review.",
            }
        )
        return {
            "next_node": ROUTING_MATRIX.get(failed_node, {}).get(
                "failure", "human_approval_hold"
            ),
            "status": "needs_human_review",
            "execution_logs": logs,
        }

    if "plan" not in state:
        command = state.get("command") or DEFAULT_COMMAND
        try:
            plan = _plan_with_llm(command)
            planner_source = "llm" if os.getenv("OPENAI_API_KEY") else "deterministic"
        except (OpenAIError, json.JSONDecodeError, ValidationError, ValueError) as error:
            logger.warning("Orchestrator planner failed; using deterministic plan: %s", error)
            plan = _fallback_plan(command)
            planner_source = "deterministic_fallback"
            logs.append(
                {
                    "agent": "Orchestrator",
                    "action": f"LLM planning failed; deterministic plan used: {error}",
                }
            )

        logs.append(
            {
                "agent": "Orchestrator",
                "action": f"Decomposed objective into {len(plan.tasks)} ordered tasks using {planner_source}.",
            }
        )
        return {
            "plan": [task.model_dump() for task in plan.tasks],
            "plan_summary": plan.plan_summary,
            "priority": plan.priority,
            "task_index": 0,
            "next_node": plan.tasks[0].node,
            "execution_logs": logs,
        }

    task_index = state.get("task_index", 0)
    tasks = state["plan"]
    if task_index >= len(tasks):
        return {
            "next_node": "end",
            "status": "completed",
            "execution_logs": logs,
        }

    next_node = tasks[task_index]["node"]
    expected_node = (
        ROUTING_ORDER[0]
        if task_index == 0
        else ROUTING_MATRIX[tasks[task_index - 1]["node"]]["success"]
    )
    if next_node != expected_node:
        logs.append(
            {
                "agent": "Orchestrator",
                "action": f"Invalid route to {next_node}; routing to human review.",
            }
        )
        return {
            "next_node": "human_approval_hold",
            "last_error": f"Plan route {next_node} violates required order; expected {expected_node}.",
            "status": "needs_human_review",
            "execution_logs": logs,
        }
    logs.append(
        {
            "agent": "Orchestrator",
            "action": f"Routing task {task_index + 1}/{len(tasks)} to {next_node}.",
        }
    )
    return {"next_node": next_node, "execution_logs": logs}


def wrap_downstream(
    node_name: str,
    action: Callable[[SupplyChainState], dict[str, Any]],
) -> Callable[[SupplyChainState], SupplyChainState]:
    def execute(state: SupplyChainState) -> SupplyChainState:
        logs = list(state.get("execution_logs", []))
        try:
            updates = action(state)
        except Exception as error:
            logger.exception("Downstream node %s failed", node_name)
            logs.append(
                {
                    "agent": node_name,
                    "action": f"Execution failed: {type(error).__name__}: {error}",
                }
            )
            return {
                "failed_node": node_name,
                "last_error": f"{type(error).__name__}: {error}",
                "execution_logs": logs,
            }

        task_index = state.get("task_index", 0)
        completed = list(state.get("completed_tasks", []))
        completed.append(state["plan"][task_index]["description"])
        logs.append({"agent": node_name, "action": "Task completed successfully."})
        return {
            **updates,
            "task_index": task_index + 1,
            "retry_count": 0,
            "failed_node": "",
            "last_error": "",
            "completed_tasks": completed,
            "execution_logs": logs,
        }

    return execute


def human_approval_hold(state: SupplyChainState) -> SupplyChainState:
    error = state.get("last_error", "A downstream operation needs review.")
    return {
        "title": "Supply-chain workflow requires review",
        "summary": (
            f"The workflow could not safely complete objective "
            f"'{state.get('command', DEFAULT_COMMAND)}'. Last error: {error}"
        ),
        "requires_human_approval": True,
        "status": "needs_human_review",
        "next_node": "end",
    }

