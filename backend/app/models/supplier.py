from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, func
from app.core.database import Base


class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"),
                       index=True, nullable=False)
    name = Column(String, nullable=False)
    phone = Column(String)
    email = Column(String)
    debt = Column(Float, default=0)
    created_at = Column(DateTime, server_default=func.now())


class Purchase(Base):
    __tablename__ = "purchases"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"),
                       index=True, nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"))
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    total = Column(Float, default=0)
    paid = Column(Float, default=0)
    created_at = Column(DateTime, server_default=func.now())


class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), index=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    quantity = Column(Float, default=0)
    price = Column(Float, default=0)
