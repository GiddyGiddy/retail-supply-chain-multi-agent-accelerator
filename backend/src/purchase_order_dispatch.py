import logging
import math
import os
from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.orm import Session

from agents.graph.nodes.procurement_vendor import PurchaseOrderDraft
from agents.guardrails.deterministic import requires_escalated_review
from agents.tools.business_central import BusinessCentralClient, BusinessCentralConfig
from src.config.settings import settings
from src.models import Approval, PurchaseOrderDispatch, PurchaseOrderDispatchAttempt
from src.sandbox_target import (
    SandboxVerificationError,
    SandboxVerifierCredentials,
    verify_sandbox_target,
)

logger = logging.getLogger(__name__)


def _validated_draft(dispatch: PurchaseOrderDispatch) -> dict[str, Any]:
    state = dispatch.approved_payload
    try:
        if "sandbox_test" in state and not isinstance(state["sandbox_test"], bool):
            raise ValueError("Invalid sandbox test marker")
        po = PurchaseOrderDraft.model_validate(state["po_draft"])
        if (
            state.get("sourcing_strategy") != "VENDOR_PO"
            or len(po.lines) != 1
            or po.vendor_id != state.get("selected_vendor_id")
            or po.lines[0].sku != state.get("sku")
            or po.lines[0].quantity != state.get("reorder_quantity")
            or po.lines[0].unit_price != state.get("unit_cost")
            or not math.isfinite(po.lines[0].unit_price)
            or not math.isfinite(po.total_amount)
            or po.total_amount != state.get("total_estimated_cost")
            or not po.vendor_id.strip()
            or not po.lines[0].sku.strip()
        ):
            raise ValueError("Approved draft does not match sourcing facts")
        if (
            po.currency not in {"USD", "EUR"}
            or po.currency != state.get("cost_currency", "USD")
            or requires_escalated_review(po.total_amount)
        ):
            raise ValueError("Draft requires additional currency or value review")
        if state.get("sandbox_test") is True and (
            po.vendor_id != "V00010"
            or po.lines[0].sku != "1000"
            or po.lines[0].quantity != 10
            or po.lines[0].unit_price != 5.0
            or po.currency != "EUR"
            or po.ship_to_warehouse_id is not None
            or po.requested_delivery_date is not None
        ):
            raise ValueError("Sandbox test draft does not match the approved test facts")
    except (KeyError, TypeError, ValidationError, ValueError, OverflowError) as error:
        raise HTTPException(
            status_code=422,
            detail="Approved draft is invalid or requires additional review; no order was sent",
        ) from error
    payload = po.model_dump(mode="json")
    payload["po_reference"] = dispatch.reference
    return payload


def dispatch_held_order(db: Session, approval_id: str, user_subject: str) -> PurchaseOrderDispatch:
    if not settings.auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Purchase order dispatch requires authentication",
        )
    if os.getenv("BC_DISPATCH_ENABLED", "false").strip().lower() not in {"1", "true", "yes"}:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Purchase order dispatch is disabled",
        )
    try:
        config = BusinessCentralConfig.from_env()
        config.validate()
    except NotImplementedError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error

    dispatch = db.get(PurchaseOrderDispatch, approval_id)
    if dispatch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PO handoff not found")
    approval = db.get(Approval, approval_id)
    if approval is None or approval.status != "approved" or dispatch.status != "held":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PO handoff is not dispatchable")
    if approval.payload.get("sandbox_test") is True and dispatch.approved_payload.get("sandbox_test") is not True:
        raise HTTPException(status_code=422, detail="Sandbox test marker is inconsistent; no order was sent")
    payload = _validated_draft(dispatch)

    if dispatch.approved_payload.get("sandbox_test") is True:
        if os.getenv("BC_SANDBOX_DISPATCH_ENABLED", "false").strip().lower() not in {"1", "true", "yes"}:
            raise HTTPException(status_code=503, detail="Sandbox test dispatch is disabled; no order was sent")
        try:
            verify_sandbox_target(config, SandboxVerifierCredentials.from_env())
        except SandboxVerificationError as error:
            logger.warning("Sandbox target verification failed for approval %s: %s", approval_id, error)
            raise HTTPException(
                status_code=503,
                detail="Sandbox target verification failed; no order was sent",
            ) from error

    claimed = db.execute(
        update(PurchaseOrderDispatch)
        .where(PurchaseOrderDispatch.approval_id == approval_id, PurchaseOrderDispatch.status == "held")
        .values(status="in_progress")
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PO handoff is not dispatchable")
    attempt = PurchaseOrderDispatchAttempt(
        approval_id=approval_id, started_by=user_subject, outcome="in_progress",
    )
    db.add(attempt)
    db.commit()
    db.refresh(dispatch)

    def save_header(order_id: str) -> None:
        attempt.erp_order_id = order_id
        db.commit()

    try:
        client = BusinessCentralClient(config)
        try:
            result = client.create_draft_purchase_order(
                payload, on_order_created=save_header, reject_existing=True,
            )
        finally:
            client.close()
        if (
            result.get("erp_status") != "Draft"
            or result.get("total_matches") is not True
            or not result.get("erp_po_id")
            or not result.get("po_number")
            or result.get("duplicate")
            or attempt.erp_order_id != result["erp_po_id"]
        ):
            raise ValueError("Business Central draft result requires manual review")
        attempt.erp_order_number = result["po_number"]
        attempt.outcome = "created"
        dispatch.status = "created"
    except Exception:
        logger.exception("PO dispatch failed for approval %s; manual reconciliation required", approval_id)
        db.rollback()
        attempt = db.get(PurchaseOrderDispatchAttempt, approval_id)
        dispatch = db.get(PurchaseOrderDispatch, approval_id)
        attempt.outcome = "needs_review"
        dispatch.status = "needs_review"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="PO outcome is uncertain; manual reconciliation is required before any retry",
        )
    db.commit()
    db.refresh(dispatch)
    return dispatch
