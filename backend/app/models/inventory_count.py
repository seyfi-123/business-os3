from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, func
from app.core.database import Base


class InventoryCount(Base):
    __tablename__ = "inventory_counts"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"),
                       index=True, nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    expected_qty = Column(Float, default=0)
    actual_qty = Column(Float, default=0)
    variance_qty = Column(Float, default=0)
    unit_cost = Column(Float, default=0)
    variance_value = Column(Float, default=0)
    counted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    note = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
