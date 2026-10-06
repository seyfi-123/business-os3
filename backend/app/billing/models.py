from sqlalchemy import (Column, Integer, String, Float, ForeignKey,
                        DateTime, Boolean, func, Index, text)
from app.core.database import Base


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (Index("ix_subscriptions_tenant_status", "tenant_id", "status"),)
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    plan_code = Column(String, nullable=False)
    status = Column(String, nullable=False, default="TRIAL")
    started_at = Column(DateTime, server_default=func.now())
    trial_end = Column(DateTime, nullable=True)
    current_period_start = Column(DateTime, nullable=False)
    current_period_end = Column(DateTime, nullable=False)
    cancelled_at = Column(DateTime, nullable=True)
    auto_renew = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Invoice(Base):
    __tablename__ = "billing_invoices"
    __table_args__ = (Index("ix_invoices_tenant_status", "tenant_id", "status"),)
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id"), nullable=True)
    number = Column(String, nullable=False, unique=True)
    plan_code = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String, default="TJS")
    status = Column(String, nullable=False, default="DRAFT")
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    issued_at = Column(DateTime, server_default=func.now())
    due_at = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    payment_method = Column(String, nullable=True)
    note = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
