from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, func
from app.core.database import Base


class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"),
                       index=True, nullable=False)
    party_type = Column(String, nullable=False)
    party_id = Column(Integer, nullable=False, index=True)
    amount = Column(Float, default=0)
    direction = Column(String, default="IN")
    method = Column(String, default="CASH")
    note = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
