from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, func
from app.core.database import Base


class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), index=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=True)
    category = Column(String)
    amount = Column(Float, default=0)
    note = Column(String)
    created_at = Column(DateTime, server_default=func.now())
