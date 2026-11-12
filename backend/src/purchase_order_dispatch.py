import logging
import math
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session

from agents.graph.nodes.procurement_vendor import PurchaseOrderDraft
from agents.guardrails.deterministic import requires_escalated_review
from agents.tools.business_central import BusinessCentralClient, BusinessCentralConfig
from src.config.settings import settings
from src.models import Approval, PurchaseOrderDispatch, PurchaseOrderDispatchAttempt, PurchaseOrderDispatchReconciliation
from src.sandbox_target import (
    SandboxVerificationError,
    SandboxVerifierCredentials,
    verify_sandbox_target,
)

logger = logging.getLogger(__name__)


def reconcile_sandbox_order(
    db: Session, approval_id: str, user_subject: str, evidence: str, attempt_started_at: datetime,
) -> PurchaseOrderDispatch:
    if not settings.auth_enabled:
        raise HTTPException(status_code=503, detail="Purchase order reconciliation requires authentication")
    evidence = evidence.strip()
    if len(evidence) < 20:
        raise HTTPException(status_code=422, detail="Describe the Business Central checks performed")
    dispatch = db.get(PurchaseOrderDispatch, approval_id)
    if dispatch is None:
        raise HTTPException(status_code=404, detail="PO handoff not found")
    approval = db.get(Approval, approval_id)
    attempt = db.get(PurchaseOrderDispatchAttempt, approval_id)
    if attempt_started_at.utcoffset() is None:
        raise HTTPException(status_code=422, detail="Attempt timestamp must include a timezone")
    expected_time = attempt_started_at.astimezone(timezone.utc).replace(tzinfo=None)
    actual_time = None
    if attempt is not None:
        actual_time = attempt.started_at
        if actual_time.utcoffset() is not None:
            actual_time = actual_time.astimezone(timezone.utc).replace(tzinfo=None)
    if (
        approval is None or approval.status != "approved"
        or approval.payload.get("sandbox_test") is not True
        or dispatch.approved_payload.get("sandbox_test") is not True
        or dispatch.status != "needs_review"
        or attempt is None or attempt.outcome != "needs_review"
        or actual_time != expected_time
        or attempt.erp_order_id is not None or attempt.erp_order_number is not None
    ):
        raise HTTPException(status_code=409, detail="Handoff is not eligible for sandbox no-order reconciliation")
    _validated_draft(dispatch)
    claimed = db.execute(
        update(PurchaseOrderDispatch)
        .where(
            PurchaseOrderDispatch.approval_id == approval_id,
            PurchaseOrderDispatch.status == "needs_review",
            exists(select(PurchaseOrderDispatchAttempt.approval_id).where(
                PurchaseOrderDispatchAttempt.approval_id == approval_id,
                PurchaseOrderDispatchAttempt.started_at == attempt.started_at,
                PurchaseOrderDispatchAttempt.started_by == attempt.started_by,
                PurchaseOrderDispatchAttempt.outcome == "needs_review",
                PurchaseOrderDispatchAttempt.erp_order_id.is_(None),
                PurchaseOrderDispatchAttempt.erp_order_number.is_(None),
            )),
        )
        .values(status="held")
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Handoff was already reconciled")
    db.add(PurchaseOrderDispatchReconciliation(
        approval_id=approval_id, started_by=attempt.started_by, started_at=attempt.started_at,
        outcome=attempt.outcome, erp_order_id=attempt.erp_order_id,
        erp_order_number=attempt.erp_order_number, reconciled_by=user_subject, evidence=evidence,
    ))
    db.delete(attempt)
    db.commit()
    db.refresh(dispatch)
    return dispatch


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
    sandbox_test = dispatch.approved_payload.get("sandbox_test") is True
    if (approval.payload.get("sandbox_test") is True) != sandbox_test:
        raise HTTPException(status_code=422, detail="Sandbox test marker is inconsistent; no order was sent")
    if not sandbox_test and os.getenv("BC_STANDARD_DISPATCH_ENABLED", "false").strip().lower() not in {
        "1", "true", "yes",
    }:
        raise HTTPException(
            status_code=503,
            detail="Standard purchase order dispatch is disabled; no order was sent",
        )
    payload = _validated_draft(dispatch)

    if sandbox_test:
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
