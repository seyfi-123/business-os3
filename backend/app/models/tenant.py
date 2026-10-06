from sqlalchemy import Column, Integer, String, DateTime, Boolean, func
from app.core.database import Base


class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    industry = Column(String, nullable=False)
    country = Column(String, default="TJ")
    currency = Column(String, default="TJS")
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
