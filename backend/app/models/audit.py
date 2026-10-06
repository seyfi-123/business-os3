from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, JSON, func
from app.core.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"),
                       index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String, nullable=False)
    entity = Column(String, nullable=False)
    entity_id = Column(Integer, nullable=True)
    payload = Column(JSON, nullable=True)
    ip = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
