from sqlalchemy import Column, Integer, String, Float, ForeignKey
from app.core.database import Base


class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), index=True)
    name = Column(String, nullable=False)
    phone = Column(String)
    debt = Column(Float, default=0)
