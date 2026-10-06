from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, func
from app.core.database import Base


class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), index=True)
    level = Column(String)
    kind = Column(String)
    title = Column(String)
    message = Column(String)
    amount = Column(Float, default=0)
    created_at = Column(DateTime, server_default=func.now())
