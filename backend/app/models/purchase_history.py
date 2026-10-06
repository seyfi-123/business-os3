from sqlalchemy import Column, Integer, Float, ForeignKey, DateTime, func
from app.core.database import Base


class PurchasePriceHistory(Base):
    __tablename__ = "purchase_price_history"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"),
                       index=True, nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"),
                        index=True, nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"),
                         nullable=True, index=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=True)
    quantity = Column(Float, default=0)
    unit_price = Column(Float, default=0)
    total = Column(Float, default=0)
    created_at = Column(DateTime, server_default=func.now(), index=True)
