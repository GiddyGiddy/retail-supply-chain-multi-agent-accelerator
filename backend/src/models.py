from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    dispatch: Mapped["PurchaseOrderDispatch | None"] = relationship(
        back_populates="approval", uselist=False
    )


class PurchaseOrderDispatch(Base):
    __tablename__ = "purchase_order_dispatches"

    approval_id: Mapped[str] = mapped_column(ForeignKey("approvals.id"), primary_key=True)
    reference: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="held")
    approved_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    approved_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    approval: Mapped[Approval] = relationship(back_populates="dispatch")
    attempt: Mapped["PurchaseOrderDispatchAttempt | None"] = relationship(
        back_populates="dispatch", uselist=False
    )
    reconciliations: Mapped[list["PurchaseOrderDispatchReconciliation"]] = relationship(
        back_populates="dispatch", order_by="PurchaseOrderDispatchReconciliation.reconciled_at"
    )


class PurchaseOrderDispatchAttempt(Base):
    __tablename__ = "purchase_order_dispatch_attempts"

    approval_id: Mapped[str] = mapped_column(
        ForeignKey("purchase_order_dispatches.approval_id"), primary_key=True
    )
    started_by: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    erp_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    erp_order_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False, default="in_progress")
    dispatch: Mapped[PurchaseOrderDispatch] = relationship(back_populates="attempt")


class PurchaseOrderDispatchReconciliation(Base):
    __tablename__ = "purchase_order_dispatch_reconciliations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    approval_id: Mapped[str] = mapped_column(ForeignKey("purchase_order_dispatches.approval_id"), nullable=False)
    started_by: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    erp_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    erp_order_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reconciled_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reconciled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    dispatch: Mapped[PurchaseOrderDispatch] = relationship(back_populates="reconciliations")
