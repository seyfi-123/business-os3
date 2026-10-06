from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, func
from app.core.database import Base


class Sale(Base):
    __tablename__ = "sales"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), index=True)
    branch_id = Column(Integer, ForeignKey("branches.id"))
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    total = Column(Float, default=0)
    discount = Column(Float, default=0)
    payment_type = Column(String, default="CASH")
    created_at = Column(DateTime, server_default=func.now())


class SaleItem(Base):
    __tablename__ = "sale_items"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), index=True)
    sale_id = Column(Integer, ForeignKey("sales.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    quantity = Column(Float, default=0)
    price = Column(Float, default=0)
    cost = Column(Float, default=0)
