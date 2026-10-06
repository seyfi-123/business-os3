from sqlalchemy import (Column, Integer, String, Text, Boolean, ForeignKey,
                        DateTime, JSON, UniqueConstraint, Index, func)
from app.core.database import Base


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_tenant_user", "tenant_id", "user_id"),
        Index("ix_notifications_tenant_read", "tenant_id", "read_at"),
    )
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    trigger_key = Column(String, nullable=False)
    severity = Column(String, nullable=False, default="info")
    title_tj = Column(String, nullable=False)
    body_tj = Column(Text, nullable=False)
    entity_type = Column(String, nullable=True)
    entity_id = Column(Integer, nullable=True)
    payload = Column(JSON, nullable=True)
    dedup_key = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    read_at = Column(DateTime, nullable=True)


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "trigger_key",
                                        "channel", name="uq_notif_pref"),)
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    trigger_key = Column(String, nullable=False)
    channel = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        Index("ix_notif_delivery_notification", "notification_id"),
        Index("ix_notif_delivery_status", "status", "attempted_at"),
    )
    id = Column(Integer, primary_key=True)
    notification_id = Column(Integer, ForeignKey("notifications.id"),
                             nullable=False, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    channel = Column(String, nullable=False)
    status = Column(String, nullable=False, default="PENDING")
    target = Column(String, nullable=True)
    error = Column(String, nullable=True)
    attempts = Column(Integer, default=1)
    attempted_at = Column(DateTime, server_default=func.now(), index=True)
    sent_at = Column(DateTime, nullable=True)
