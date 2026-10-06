from sqlalchemy import Column, Integer, String, ForeignKey
from app.core.database import Base


class Company(Base):
    __tablename__ = "companies"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), index=True)
    name = Column(String, nullable=False)
    currency = Column(String, default="TJS")
    country = Column(String, default="TJ")


class Branch(Base):
    __tablename__ = "branches"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), index=True)
    company_id = Column(Integer, ForeignKey("companies.id"))
    name = Column(String, nullable=False)
    kind = Column(String, default="BRANCH")
