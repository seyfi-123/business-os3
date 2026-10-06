"""
Business OS — Auto installer
Round 1: Core + Models
"""
import os
import subprocess

FILES = {}

FILES["backend/app/__init__.py"] = ""

FILES["backend/app/core/__init__.py"] = ""

FILES["backend/app/core/config.py"] = '''from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "Business OS"
    APP_TAGLINE_TJ: str = "Biznesingizni boshqaring. Yoqotishlarni toping. Keyingi qadamni oldindan biling."
    SECRET_KEY: str = "CHANGE_ME_IN_PRODUCTION"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/business_os"
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"

    class Config:
        env_file = ".env"


settings = Settings()
'''

FILES["backend/app/core/database.py"] = '''from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.core.config import settings

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
'''

FILES["backend/app/core/security.py"] = '''from datetime import datetime, timedelta
from jose import jwt, JWTError
from passlib.context import CryptContext

from app.core.config import settings

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(p: str) -> str:
    return pwd_ctx.hash(p)


def verify_password(p: str, h: str) -> bool:
    return pwd_ctx.verify(p, h)


def create_token(data: dict) -> str:
    d = data.copy()
    d["exp"] = datetime.utcnow() + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    return jwt.encode(d, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str):
    try:
        return jwt.decode(token, settings.SECRET_KEY,
                          algorithms=[settings.ALGORITHM])
    except JWTError:
        return None
'''

FILES["backend/app/core/tx.py"] = '''from contextlib import contextmanager
from sqlalchemy.orm import Session


@contextmanager
def transaction(db: Session):
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
'''

FILES["backend/app/core/permissions.py"] = '''PERMISSIONS = {
    "OWNER": {"*": ["*"]},
    "ADMIN": {"*": ["*"]},
    "MANAGER": {
        "sales": ["view", "create", "edit", "approve", "export"],
        "inventory": ["view", "create", "edit", "adjust", "export"],
        "purchases": ["view", "create", "edit", "approve"],
        "products": ["view", "create", "edit"],
        "customers": ["view", "create", "edit"],
        "suppliers": ["view", "create", "edit"],
        "finance": ["view", "export"],
        "reports": ["view", "export"],
        "intelligence": ["view"],
        "ai": ["use"],
    },
    "CASHIER": {
        "sales": ["view", "create"],
        "customers": ["view", "create"],
        "products": ["view"],
        "inventory": ["view"],
        "reports": ["view"],
    },
    "WAREHOUSE": {
        "inventory": ["view", "create", "edit", "adjust", "transfer", "count"],
        "products": ["view", "create", "edit"],
        "purchases": ["view", "create"],
        "suppliers": ["view"],
    },
    "ACCOUNTANT": {
        "finance": ["view", "create", "edit", "export"],
        "sales": ["view", "export"],
        "purchases": ["view", "export"],
        "customers": ["view", "edit"],
        "suppliers": ["view", "edit"],
        "reports": ["view", "export"],
        "intelligence": ["view"],
    },
    "ANALYST": {
        "sales": ["view", "export"],
        "inventory": ["view", "export"],
        "finance": ["view", "export"],
        "reports": ["view", "export"],
        "intelligence": ["view"],
        "ai": ["use"],
    },
}


def can(role: str, resource: str, action: str) -> bool:
    if role not in PERMISSIONS:
        return False
    rules = PERMISSIONS[role]
    if "*" in rules and "*" in rules["*"]:
        return True
    actions = rules.get(resource, [])
    return "*" in actions or action in actions
'''

FILES["backend/app/core/deps.py"] = '''from fastapi import Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.core.permissions import can
from app.models.user import User
from app.models.audit import AuditLog


def current_user(authorization: str = Header(None),
                 db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Avtorizatsiya talab qilinadi")
    payload = decode_token(authorization.split(" ")[1])
    if not payload:
        raise HTTPException(401, "Token yaroqsiz")
    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if not user:
        raise HTTPException(401, "Foydalanuvchi topilmadi")
    return user


def require(resource: str, action: str):
    def checker(user: User = Depends(current_user)):
        if not can(user.role, resource, action):
            raise HTTPException(403, f"Ruxsat yoq: {resource}.{action}")
        return user
    return checker


def audit(db: Session, tenant_id: int, user_id, action: str,
          entity: str, entity_id=None, payload=None, ip=None):
    db.add(AuditLog(tenant_id=tenant_id, user_id=user_id, action=action,
                    entity=entity, entity_id=entity_id, payload=payload, ip=ip))
'''

FILES["backend/app/core/validators.py"] = '''from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.models.company import Branch
from app.models.product import Product
from app.models.supplier import Supplier
from app.models.customer import Customer


def ensure_branch(db: Session, tenant_id: int, branch_id: int) -> Branch:
    b = db.query(Branch).filter(Branch.id == branch_id,
                                 Branch.tenant_id == tenant_id).first()
    if not b:
        raise HTTPException(404, f"Filial topilmadi: {branch_id}")
    return b


def ensure_product(db: Session, tenant_id: int, product_id: int) -> Product:
    p = db.query(Product).filter(Product.id == product_id,
                                  Product.tenant_id == tenant_id).first()
    if not p:
        raise HTTPException(404, f"Mahsulot topilmadi: {product_id}")
    return p


def ensure_supplier(db: Session, tenant_id: int, supplier_id: int) -> Supplier:
    s = db.query(Supplier).filter(Supplier.id == supplier_id,
                                   Supplier.tenant_id == tenant_id).first()
    if not s:
        raise HTTPException(404, f"Supplier topilmadi: {supplier_id}")
    return s


def ensure_customer(db: Session, tenant_id: int, customer_id: int) -> Customer:
    c = db.query(Customer).filter(Customer.id == customer_id,
                                   Customer.tenant_id == tenant_id).first()
    if not c:
        raise HTTPException(404, f"Mijoz topilmadi: {customer_id}")
    return c
'''

FILES["backend/app/models/__init__.py"] = '''from app.models.tenant import Tenant
from app.models.user import User
from app.models.company import Company, Branch
from app.models.product import Product, Inventory, InventoryMovement
from app.models.sale import Sale, SaleItem
from app.models.customer import Customer
from app.models.finance import Expense
from app.models.supplier import Supplier, Purchase, PurchaseItem
from app.models.audit import AuditLog
from app.models.purchase_history import PurchasePriceHistory
from app.models.inventory_count import InventoryCount
from app.models.payment import Payment
from app.models.alert import Alert

__all__ = [
    "Tenant", "User", "Company", "Branch",
    "Product", "Inventory", "InventoryMovement",
    "Sale", "SaleItem", "Customer", "Expense",
    "Supplier", "Purchase", "PurchaseItem",
    "AuditLog", "PurchasePriceHistory",
    "InventoryCount", "Payment", "Alert",
]
'''

FILES["backend/app/models/tenant.py"] = '''from sqlalchemy import Column, Integer, String, DateTime, Boolean, func
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
'''

FILES["backend/app/models/user.py"] = '''from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, func
from app.core.database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"),
                       index=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="OWNER")
    created_at = Column(DateTime, server_default=func.now())
'''

FILES["backend/app/models/company.py"] = '''from sqlalchemy import Column, Integer, String, ForeignKey
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
'''

FILES["backend/app/models/product.py"] = '''from sqlalchemy import (
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
'''

FILES["backend/app/models/sale.py"] = '''from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, func
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
'''

FILES["backend/app/models/customer.py"] = '''from sqlalchemy import Column, Integer, String, Float, ForeignKey
from app.core.database import Base


class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), index=True)
    name = Column(String, nullable=False)
    phone = Column(String)
    debt = Column(Float, default=0)
'''

FILES["backend/app/models/finance.py"] = '''from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, func
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
'''

FILES["backend/app/models/supplier.py"] = '''from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, func
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
'''

FILES["backend/app/models/audit.py"] = '''from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, JSON, func
from app.core.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"),
                       index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String, nullable=False)
    entity = Column(String, nullable=False)
    entity_id = Column(Integer, nullable=True)
    payload = Column(JSON, nullable=True)
    ip = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
'''

FILES["backend/app/models/purchase_history.py"] = '''from sqlalchemy import Column, Integer, Float, ForeignKey, DateTime, func
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
'''

FILES["backend/app/models/inventory_count.py"] = '''from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, func
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
'''

FILES["backend/app/models/payment.py"] = '''from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, func
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
'''

FILES["backend/app/models/alert.py"] = '''from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, func
from app.core.database import Base


class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), index=True)
    level = Column(String)
    kind = Column(String)
    title = Column(String)
    message = Column(String)
    amount = Column(Float, default=0)
    created_at = Column(DateTime, server_default=func.now())
'''


def main():
    count = 0
    for path, content in FILES.items():
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        count += 1
        print(f"✅ {path}")

    print(f"\n🎉 {count} ta fayl yaratildi")

    subprocess.run(["git", "add", "-A"], check=False)
    subprocess.run(["git", "commit", "-m", "Round 1: Core + Models"], check=False)
    subprocess.run(["git", "push", "origin", "main"], check=False)
    print("🚀 GitHub'ga yuborildi")


if __name__ == "__main__":
    main()
