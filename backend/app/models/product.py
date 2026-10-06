from sqlalchemy import (
    Column, Integer, String, Float, ForeignKey, DateTime,
    Boolean, UniqueConstraint, func,
)
from app.core.database import Base


class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"),
                       index=True, nullable=False)
    sku = Column(String, index=True)
    name = Column(String, nullable=False)
    category = Column(String)
    cost_price = Column(Float, default=0)
    sale_price = Column(Float, default=0)
    active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now())


class Inventory(Base):
    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint("tenant_id", "branch_id", "product_id",
                         name="uq_inventory_tenant_branch_product"),
    )
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"),
                       index=True, nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Float, default=0)
    updated_at = Column(DateTime, server_default=func.now(),
                        onupdate=func.now())


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), index=True)
    branch_id = Column(Integer, ForeignKey("branches.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    delta = Column(Float, nullable=False)
    reason = Column(String)
    ref_id = Column(Integer, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
