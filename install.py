"""
Business OS — Auto installer
Round 2a: Services + Schemas + API
"""
import os
import subprocess

FILES = {}

# ==================== SERVICES ====================

FILES["backend/app/services/__init__.py"] = ""

FILES["backend/app/services/inventory.py"] = '''from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.product import Inventory


def get_or_create_inventory(db: Session, tenant_id: int,
                            branch_id: int, product_id: int,
                            lock: bool = False) -> Inventory:
    q = db.query(Inventory).filter(
        Inventory.tenant_id == tenant_id,
        Inventory.branch_id == branch_id,
        Inventory.product_id == product_id,
    )
    if lock:
        q = q.with_for_update()
    inv = q.first()
    if inv:
        return inv

    stmt = (
        pg_insert(Inventory)
        .values(tenant_id=tenant_id, branch_id=branch_id,
                product_id=product_id, quantity=0)
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "branch_id", "product_id"]
        )
    )
    db.execute(stmt)
    db.flush()

    q2 = db.query(Inventory).filter(
        Inventory.tenant_id == tenant_id,
        Inventory.branch_id == branch_id,
        Inventory.product_id == product_id,
    )
    if lock:
        q2 = q2.with_for_update()
    return q2.one()
'''

FILES["backend/app/services/sales.py"] = '''from collections import defaultdict
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.models.sale import Sale, SaleItem
from app.models.product import Product, InventoryMovement
from app.models.customer import Customer
from app.models.audit import AuditLog
from app.core.validators import ensure_branch, ensure_customer
from app.core.tx import transaction
from app.services.inventory import get_or_create_inventory


def _aggregate_items(items):
    acc = defaultdict(lambda: {"quantity": 0.0, "price": None})
    for it in items:
        pid = it["product_id"]
        acc[pid]["quantity"] += float(it["quantity"])
        if it.get("price") is not None:
            acc[pid]["price"] = float(it["price"])
    return [{"product_id": pid, "quantity": v["quantity"],
             "price": v["price"]} for pid, v in acc.items()]


def create_sale(db: Session, tenant_id: int, branch_id: int,
                customer_id, items, discount: float, payment_type: str,
                user_id=None, ip=None) -> Sale:
    if not items:
        raise HTTPException(400, "Sotuv bosh bolishi mumkin emas")
    if discount < 0:
        raise HTTPException(400, "Skidka manfiy")
    if payment_type not in ("CASH", "CARD", "CREDIT"):
        raise HTTPException(400, "Tolov turi notogri")

    ensure_branch(db, tenant_id, branch_id)

    if payment_type == "CREDIT":
        if not customer_id:
            raise HTTPException(400, "Qarzga sotuv uchun mijoz shart")
        ensure_customer(db, tenant_id, customer_id)
    elif customer_id:
        ensure_customer(db, tenant_id, customer_id)

    items = _aggregate_items(items)
    for it in items:
        if it["quantity"] <= 0:
            raise HTTPException(400, "Miqdor 0 dan katta bolishi kerak")

    pid_set = {it["product_id"] for it in items}
    found = db.query(Product).filter(
        Product.tenant_id == tenant_id, Product.id.in_(pid_set)
    ).all()
    if len(found) != len(pid_set):
        raise HTTPException(404, "Bazi mahsulotlar topilmadi")
    for p in found:
        if not p.active:
            raise HTTPException(400, f"Mahsulot arxivlangan: {p.name}")

    with transaction(db):
        products_locked = {}
        for pid in sorted(pid_set):
            p = db.query(Product).filter(
                Product.id == pid, Product.tenant_id == tenant_id
            ).with_for_update().first()
            products_locked[pid] = p

        subtotal = 0.0
        prepared = []
        for it in items:
            pid = it["product_id"]
            qty = it["quantity"]
            product = products_locked[pid]
            price = float(it["price"] if it["price"] is not None
                          else product.sale_price)
            if price < 0:
                raise HTTPException(400, "Narx manfiy")
            inv = get_or_create_inventory(db, tenant_id, branch_id, pid,
                                          lock=True)
            if inv.quantity < qty:
                raise HTTPException(400,
                    f"Omborda yetarli emas: {product.name}")
            subtotal += price * qty
            prepared.append((product, inv, qty, price))

        if discount > subtotal:
            raise HTTPException(400, "Skidka jamidan katta")

        sale = Sale(
            tenant_id=tenant_id, branch_id=branch_id,
            customer_id=customer_id,
            total=round(subtotal - discount, 2),
            discount=discount, payment_type=payment_type,
        )
        db.add(sale)
        db.flush()

        for product, inv, qty, price in prepared:
            db.add(SaleItem(
                tenant_id=tenant_id, sale_id=sale.id,
                product_id=product.id, quantity=qty,
                price=price, cost=product.cost_price or 0,
            ))
            inv.quantity -= qty
            db.add(InventoryMovement(
                tenant_id=tenant_id, branch_id=branch_id,
                product_id=product.id, delta=-qty,
                reason="SALE", ref_id=sale.id, user_id=user_id,
            ))

        if payment_type == "CREDIT" and customer_id:
            c = db.query(Customer).filter(
                Customer.id == customer_id,
                Customer.tenant_id == tenant_id,
            ).first()
            c.debt = (c.debt or 0) + sale.total

        db.add(AuditLog(
            tenant_id=tenant_id, user_id=user_id, action="CREATE",
            entity="Sale", entity_id=sale.id,
            payload={"total": sale.total, "items": len(prepared)},
            ip=ip,
        ))

    db.refresh(sale)
    return sale
'''

FILES["backend/app/services/purchase.py"] = '''from collections import defaultdict
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func
from fastapi import HTTPException

from app.models.supplier import Supplier, Purchase, PurchaseItem
from app.models.product import Product, InventoryMovement
from app.models.purchase_history import PurchasePriceHistory
from app.models.audit import AuditLog
from app.core.validators import ensure_branch, ensure_supplier
from app.core.tx import transaction
from app.services.inventory import get_or_create_inventory


def weighted_avg_cost(db: Session, product_id: int, days: int = 90) -> float:
    since = datetime.utcnow() - timedelta(days=days)
    row = db.query(
        func.coalesce(func.sum(PurchasePriceHistory.total), 0),
        func.coalesce(func.sum(PurchasePriceHistory.quantity), 0),
    ).filter(
        PurchasePriceHistory.product_id == product_id,
        PurchasePriceHistory.created_at >= since,
    ).first()
    total_val, total_qty = float(row[0] or 0), float(row[1] or 0)
    if total_qty == 0:
        p = db.query(Product).filter(Product.id == product_id).first()
        return (p.cost_price or 0) if p else 0
    return total_val / total_qty


def _aggregate(items):
    acc = defaultdict(float)
    for it in items:
        key = (it["product_id"], float(it["price"]))
        acc[key] += float(it["quantity"])
    return [{"product_id": pid, "price": price, "quantity": qty}
            for (pid, price), qty in acc.items()]


def create_purchase(db: Session, tenant_id: int, branch_id: int,
                    supplier_id, items, paid: float,
                    user_id=None, ip=None) -> Purchase:
    if not items:
        raise HTTPException(400, "Xarid bosh")
    if paid < 0:
        raise HTTPException(400, "Tolangan summa manfiy")

    ensure_branch(db, tenant_id, branch_id)
    if supplier_id:
        ensure_supplier(db, tenant_id, supplier_id)

    items = _aggregate(items)
    pid_set = {it["product_id"] for it in items}
    found = db.query(Product).filter(
        Product.tenant_id == tenant_id, Product.id.in_(pid_set)
    ).all()
    if len(found) != len(pid_set):
        raise HTTPException(404, "Bazi mahsulotlar topilmadi")

    total = 0.0
    for it in items:
        if it["quantity"] <= 0 or it["price"] < 0:
            raise HTTPException(400, "Miqdor > 0, narx >= 0")
        total += it["quantity"] * it["price"]

    if paid > total:
        raise HTTPException(400, "Tolov jamidan katta")

    with transaction(db):
        purchase = Purchase(
            tenant_id=tenant_id, branch_id=branch_id,
            supplier_id=supplier_id, total=total, paid=paid,
        )
        db.add(purchase)
        db.flush()

        for it in items:
            db.add(PurchaseItem(
                tenant_id=tenant_id, purchase_id=purchase.id,
                product_id=it["product_id"],
                quantity=it["quantity"], price=it["price"],
            ))
            db.add(PurchasePriceHistory(
                tenant_id=tenant_id, product_id=it["product_id"],
                supplier_id=supplier_id, purchase_id=purchase.id,
                quantity=it["quantity"], unit_price=it["price"],
                total=it["quantity"] * it["price"],
            ))
            inv = get_or_create_inventory(db, tenant_id, branch_id,
                                          it["product_id"], lock=True)
            inv.quantity += it["quantity"]
            db.add(InventoryMovement(
                tenant_id=tenant_id, branch_id=branch_id,
                product_id=it["product_id"], delta=it["quantity"],
                reason="PURCHASE", ref_id=purchase.id, user_id=user_id,
            ))

        for it in items:
            db.flush()
            p = db.query(Product).filter(
                Product.id == it["product_id"]).first()
            p.cost_price = weighted_avg_cost(db, p.id)

        debt = total - paid
        if debt > 0 and supplier_id:
            s = db.query(Supplier).filter(Supplier.id == supplier_id).first()
            s.debt = (s.debt or 0) + debt

        db.add(AuditLog(
            tenant_id=tenant_id, user_id=user_id, action="CREATE",
            entity="Purchase", entity_id=purchase.id,
            payload={"total": total, "paid": paid, "items": len(items)},
            ip=ip,
        ))

    db.refresh(purchase)
    return purchase
'''

# ==================== SCHEMAS ====================

FILES["backend/app/schemas/__init__.py"] = ""

FILES["backend/app/schemas/auth.py"] = '''from pydantic import BaseModel, EmailStr


class RegisterIn(BaseModel):
    business_type: str
    company_name: str
    branches_count: int = 1
    employees_count: int = 1
    currency: str = "TJS"
    country: str = "TJ"
    owner_email: EmailStr
    owner_name: str
    password: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
'''

# ==================== API ====================

FILES["backend/app/api/__init__.py"] = ""

FILES["backend/app/api/auth.py"] = '''from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import hash_password, verify_password, create_token
from app.models.tenant import Tenant
from app.models.user import User
from app.models.company import Company, Branch
from app.schemas.auth import RegisterIn, LoginIn, TokenOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut)
def register(data: RegisterIn, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == data.owner_email).first():
        raise HTTPException(400, "Bu email allaqachon royxatdan otgan")

    tenant = Tenant(name=data.company_name, industry=data.business_type,
                    country=data.country, currency=data.currency)
    db.add(tenant)
    db.flush()

    company = Company(tenant_id=tenant.id, name=data.company_name,
                      currency=data.currency, country=data.country)
    db.add(company)
    db.flush()

    for i in range(max(1, data.branches_count)):
        db.add(Branch(tenant_id=tenant.id, company_id=company.id,
                      name=f"Filial {i+1}", kind="BRANCH"))

    user = User(tenant_id=tenant.id, email=data.owner_email,
                full_name=data.owner_name, role="OWNER",
                password_hash=hash_password(data.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_token({"sub": str(user.id), "tenant": tenant.id,
                          "role": user.role})
    return {"access_token": token}


@router.post("/login", response_model=TokenOut)
def login(data: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == data.email).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "Email yoki parol xato")
    token = create_token({"sub": str(user.id), "tenant": user.tenant_id,
                          "role": user.role})
    return {"access_token": token}
'''

FILES["backend/app/api/tenants.py"] = '''from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import current_user
from app.models.user import User
from app.models.tenant import Tenant

router = APIRouter(prefix="/api/tenants", tags=["tenants"])


INDUSTRIES_TJ = [
    {"code": "retail", "label": "Dukon / Retail"},
    {"code": "restaurant", "label": "Restoran / Kafe"},
    {"code": "pharmacy", "label": "Apteka"},
    {"code": "fashion", "label": "Kiyim-kechak"},
    {"code": "auto", "label": "Avtoservis"},
    {"code": "construction", "label": "Qurilish"},
    {"code": "wholesale", "label": "Distribyutor"},
    {"code": "beauty", "label": "Salon / Beauty"},
    {"code": "manufacturing", "label": "Ishlab chiqarish"},
    {"code": "other", "label": "Boshqa"},
]


@router.get("/industries")
def industries():
    return INDUSTRIES_TJ


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    return {
        "tenant": {
            "id": tenant.id, "name": tenant.name,
            "industry": tenant.industry,
            "currency": tenant.currency, "country": tenant.country,
        },
        "user": {"id": user.id, "name": user.full_name, "role": user.role},
    }
'''

FILES["backend/app/api/products.py"] = '''from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.core.deps import require
from app.core.tx import transaction
from app.core.validators import ensure_product
from app.models.user import User
from app.models.product import Product
from app.models.audit import AuditLog

router = APIRouter(prefix="/api/products", tags=["products"])


class ProductIn(BaseModel):
    sku: str | None = None
    name: str = Field(min_length=1)
    category: str | None = None
    cost_price: float = Field(default=0, ge=0)
    sale_price: float = Field(default=0, ge=0)


@router.get("")
def list_products(q: str | None = None, include_inactive: bool = False,
                  user: User = Depends(require("products", "view")),
                  db: Session = Depends(get_db)):
    query = db.query(Product).filter(Product.tenant_id == user.tenant_id)
    if not include_inactive:
        query = query.filter(Product.active.is_(True))
    if q:
        query = query.filter(Product.name.ilike(f"%{q}%"))
    return [{"id": p.id, "sku": p.sku, "name": p.name, "category": p.category,
             "cost_price": p.cost_price, "sale_price": p.sale_price,
             "active": p.active}
            for p in query.order_by(Product.name).limit(200).all()]


@router.post("")
def create(data: ProductIn,
           user: User = Depends(require("products", "create")),
           db: Session = Depends(get_db)):
    with transaction(db):
        p = Product(tenant_id=user.tenant_id, **data.model_dump())
        db.add(p)
        db.flush()
        db.add(AuditLog(tenant_id=user.tenant_id, user_id=user.id,
                        action="CREATE", entity="Product", entity_id=p.id))
    return {"id": p.id, "name": p.name}


@router.put("/{pid}")
def update(pid: int, data: ProductIn,
           user: User = Depends(require("products", "edit")),
           db: Session = Depends(get_db)):
    with transaction(db):
        p = ensure_product(db, user.tenant_id, pid)
        for k, v in data.model_dump().items():
            setattr(p, k, v)
    return {"ok": True}


@router.delete("/{pid}")
def delete(pid: int,
           user: User = Depends(require("products", "delete")),
           db: Session = Depends(get_db)):
    with transaction(db):
        p = ensure_product(db, user.tenant_id, pid)
        p.active = False
    return {"ok": True, "active": False}


@router.post("/{pid}/restore")
def restore(pid: int,
            user: User = Depends(require("products", "edit")),
            db: Session = Depends(get_db)):
    with transaction(db):
        p = ensure_product(db, user.tenant_id, pid)
        p.active = True
    return {"ok": True, "active": True}
'''

FILES["backend/app/api/sales.py"] = '''from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.core.deps import require
from app.models.user import User
from app.models.sale import Sale, SaleItem
from app.services.sales import create_sale

router = APIRouter(prefix="/api/sales", tags=["sales"])


class SaleItemIn(BaseModel):
    product_id: int
    quantity: float = Field(gt=0)
    price: float | None = Field(default=None, ge=0)


class SaleIn(BaseModel):
    branch_id: int
    customer_id: int | None = None
    items: list[SaleItemIn] = Field(min_length=1)
    discount: float = Field(default=0, ge=0)
    payment_type: str = Field(default="CASH", pattern="^(CASH|CARD|CREDIT)$")


@router.post("")
def create(data: SaleIn, request: Request,
           user: User = Depends(require("sales", "create")),
           db: Session = Depends(get_db)):
    sale = create_sale(
        db, user.tenant_id, data.branch_id, data.customer_id,
        [i.model_dump() for i in data.items],
        data.discount, data.payment_type, user.id,
        ip=request.client.host if request.client else None,
    )
    return {"id": sale.id, "total": sale.total}


@router.get("")
def list_sales(limit: int = 50, branch_id: int | None = None,
               user: User = Depends(require("sales", "view")),
               db: Session = Depends(get_db)):
    q = db.query(Sale).filter(Sale.tenant_id == user.tenant_id)
    if branch_id:
        q = q.filter(Sale.branch_id == branch_id)
    rows = q.order_by(Sale.created_at.desc()).limit(limit).all()
    return [{"id": s.id, "total": s.total, "discount": s.discount,
             "payment_type": s.payment_type, "branch_id": s.branch_id,
             "customer_id": s.customer_id,
             "created_at": s.created_at} for s in rows]


@router.get("/{sale_id}")
def detail(sale_id: int,
           user: User = Depends(require("sales", "view")),
           db: Session = Depends(get_db)):
    s = db.query(Sale).filter(Sale.id == sale_id,
                              Sale.tenant_id == user.tenant_id).first()
    if not s:
        raise HTTPException(404, "Sotuv topilmadi")
    items = db.query(SaleItem).filter(SaleItem.sale_id == s.id).all()
    return {"id": s.id, "total": s.total, "discount": s.discount,
            "payment_type": s.payment_type, "created_at": s.created_at,
            "items": [{"product_id": i.product_id, "quantity": i.quantity,
                       "price": i.price, "cost": i.cost} for i in items]}
'''

FILES["backend/app/api/inventory.py"] = '''from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.core.deps import require
from app.core.tx import transaction
from app.core.validators import ensure_branch, ensure_product
from app.models.user import User
from app.models.product import Product, Inventory, InventoryMovement
from app.models.inventory_count import InventoryCount
from app.models.audit import AuditLog

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


class AdjustIn(BaseModel):
    branch_id: int
    product_id: int
    delta: float
    reason: str = Field(default="ADJUST", pattern="^(ADJUST|RETURN|OTHER)$")


class CountIn(BaseModel):
    branch_id: int
    product_id: int
    actual_qty: float = Field(ge=0)
    note: str | None = None


@router.get("")
def stock(branch_id: int | None = None,
          user: User = Depends(require("inventory", "view")),
          db: Session = Depends(get_db)):
    q = (db.query(Product, Inventory)
         .join(Inventory, (Inventory.product_id == Product.id)
                          & (Inventory.tenant_id == Product.tenant_id))
         .filter(Product.tenant_id == user.tenant_id))
    if branch_id:
        ensure_branch(db, user.tenant_id, branch_id)
        q = q.filter(Inventory.branch_id == branch_id)
    return [{"product_id": p.id, "sku": p.sku, "name": p.name,
             "branch_id": inv.branch_id, "quantity": inv.quantity,
             "cost_price": p.cost_price, "sale_price": p.sale_price}
            for p, inv in q.all()]


@router.post("/adjust")
def adjust(data: AdjustIn,
           user: User = Depends(require("inventory", "adjust")),
           db: Session = Depends(get_db)):
    if data.delta == 0:
        raise HTTPException(400, "Delta 0")
    with transaction(db):
        ensure_branch(db, user.tenant_id, data.branch_id)
        ensure_product(db, user.tenant_id, data.product_id)
        inv = db.query(Inventory).filter(
            Inventory.tenant_id == user.tenant_id,
            Inventory.branch_id == data.branch_id,
            Inventory.product_id == data.product_id,
        ).with_for_update().first()
        if not inv:
            inv = Inventory(tenant_id=user.tenant_id,
                            branch_id=data.branch_id,
                            product_id=data.product_id, quantity=0)
            db.add(inv)
            db.flush()
        if inv.quantity + data.delta < 0:
            raise HTTPException(400, "Zaxira manfiy")
        inv.quantity += data.delta
        db.add(InventoryMovement(
            tenant_id=user.tenant_id, branch_id=data.branch_id,
            product_id=data.product_id, delta=data.delta,
            reason=data.reason, user_id=user.id,
        ))
    return {"product_id": data.product_id, "new_quantity": inv.quantity}


@router.post("/count")
def physical_count(data: CountIn,
                   user: User = Depends(require("inventory", "count")),
                   db: Session = Depends(get_db)):
    with transaction(db):
        ensure_branch(db, user.tenant_id, data.branch_id)
        product = ensure_product(db, user.tenant_id, data.product_id)
        inv = db.query(Inventory).filter(
            Inventory.tenant_id == user.tenant_id,
            Inventory.branch_id == data.branch_id,
            Inventory.product_id == data.product_id,
        ).with_for_update().first()
        expected = inv.quantity if inv else 0
        variance = expected - data.actual_qty
        unit_cost = product.cost_price or 0
        variance_value = variance * unit_cost
        db.add(InventoryCount(
            tenant_id=user.tenant_id, branch_id=data.branch_id,
            product_id=data.product_id, expected_qty=expected,
            actual_qty=data.actual_qty, variance_qty=variance,
            unit_cost=unit_cost, variance_value=variance_value,
            counted_by=user.id, note=data.note,
        ))
        if inv:
            inv.quantity = data.actual_qty
        else:
            db.add(Inventory(tenant_id=user.tenant_id,
                             branch_id=data.branch_id,
                             product_id=data.product_id,
                             quantity=data.actual_qty))
        db.add(InventoryMovement(
            tenant_id=user.tenant_id, branch_id=data.branch_id,
            product_id=data.product_id, delta=-variance,
            reason="COUNT", user_id=user.id,
        ))
    return {"expected": expected, "actual": data.actual_qty,
            "variance_qty": variance,
            "variance_value": round(variance_value, 2)}


@router.get("/movements")
def movements(limit: int = 100, branch_id: int | None = None,
              user: User = Depends(require("inventory", "view")),
              db: Session = Depends(get_db)):
    q = db.query(InventoryMovement).filter(
        InventoryMovement.tenant_id == user.tenant_id)
    if branch_id:
        q = q.filter(InventoryMovement.branch_id == branch_id)
    rows = q.order_by(InventoryMovement.created_at.desc()).limit(limit).all()
    return [{"id": m.id, "branch_id": m.branch_id,
             "product_id": m.product_id, "delta": m.delta,
             "reason": m.reason, "user_id": m.user_id,
             "created_at": m.created_at} for m in rows]
'''

FILES["backend/app/api/crm.py"] = '''from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.core.deps import require
from app.core.tx import transaction
from app.core.validators import ensure_customer
from app.models.user import User
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.sale import Sale

router = APIRouter(prefix="/api/customers", tags=["crm"])


class CustomerIn(BaseModel):
    name: str
    phone: str | None = None


class PayIn(BaseModel):
    amount: float = Field(gt=0)
    method: str = Field(default="CASH", pattern="^(CASH|CARD|BANK)$")


@router.get("")
def list_c(user: User = Depends(require("customers", "view")),
           db: Session = Depends(get_db)):
    rows = (db.query(Customer)
            .filter(Customer.tenant_id == user.tenant_id)
            .order_by(Customer.name).limit(500).all())
    return [{"id": c.id, "name": c.name, "phone": c.phone,
             "debt": c.debt} for c in rows]


@router.post("")
def create(data: CustomerIn,
           user: User = Depends(require("customers", "create")),
           db: Session = Depends(get_db)):
    with transaction(db):
        c = Customer(tenant_id=user.tenant_id, **data.model_dump())
        db.add(c)
        db.flush()
    return {"id": c.id}


@router.post("/{cid}/pay")
def pay(cid: int, data: PayIn,
        user: User = Depends(require("customers", "edit")),
        db: Session = Depends(get_db)):
    with transaction(db):
        c = ensure_customer(db, user.tenant_id, cid)
        if data.amount > (c.debt or 0):
            raise HTTPException(400, "Tolov qarzdan katta")
        c.debt -= data.amount
        db.add(Payment(tenant_id=user.tenant_id, party_type="CUSTOMER",
                       party_id=cid, amount=data.amount,
                       direction="IN", method=data.method))
    return {"id": c.id, "debt": c.debt}
'''

FILES["backend/app/api/suppliers.py"] = '''from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.core.deps import require
from app.core.tx import transaction
from app.core.validators import ensure_supplier
from app.models.user import User
from app.models.supplier import Supplier
from app.models.payment import Payment
from app.services.purchase import create_purchase

router = APIRouter(prefix="/api/suppliers", tags=["suppliers"])


class SupplierIn(BaseModel):
    name: str
    phone: str | None = None
    email: str | None = None


class PurchaseItemIn(BaseModel):
    product_id: int
    quantity: float = Field(gt=0)
    price: float = Field(ge=0)


class PurchaseIn(BaseModel):
    branch_id: int
    supplier_id: int | None = None
    items: list[PurchaseItemIn] = Field(min_length=1)
    paid: float = Field(default=0, ge=0)


class PayIn(BaseModel):
    amount: float = Field(gt=0)
    method: str = Field(default="CASH", pattern="^(CASH|CARD|BANK)$")


@router.get("")
def list_s(user: User = Depends(require("suppliers", "view")),
           db: Session = Depends(get_db)):
    rows = db.query(Supplier).filter(Supplier.tenant_id == user.tenant_id).all()
    return [{"id": s.id, "name": s.name, "phone": s.phone,
             "debt": s.debt} for s in rows]


@router.post("")
def create(data: SupplierIn,
           user: User = Depends(require("suppliers", "create")),
           db: Session = Depends(get_db)):
    with transaction(db):
        s = Supplier(tenant_id=user.tenant_id, **data.model_dump())
        db.add(s)
        db.flush()
    return {"id": s.id}


@router.post("/purchases")
def purchase(data: PurchaseIn, request: Request,
             user: User = Depends(require("purchases", "create")),
             db: Session = Depends(get_db)):
    p = create_purchase(
        db, user.tenant_id, data.branch_id, data.supplier_id,
        [i.model_dump() for i in data.items], data.paid, user.id,
        ip=request.client.host if request.client else None,
    )
    return {"id": p.id, "total": p.total, "paid": p.paid,
            "debt": p.total - p.paid}


@router.post("/{sid}/pay")
def pay_supplier(sid: int, data: PayIn,
                 user: User = Depends(require("suppliers", "edit")),
                 db: Session = Depends(get_db)):
    with transaction(db):
        s = ensure_supplier(db, user.tenant_id, sid)
        if data.amount > (s.debt or 0):
            raise HTTPException(400, "Tolov qarzdan katta")
        s.debt -= data.amount
        db.add(Payment(tenant_id=user.tenant_id, party_type="SUPPLIER",
                       party_id=sid, amount=data.amount,
                       direction="OUT", method=data.method))
    return {"id": s.id, "debt": s.debt}
'''

FILES["backend/app/api/finance.py"] = '''from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel

from app.core.database import get_db
from app.core.deps import require
from app.models.user import User
from app.models.finance import Expense
from app.models.sale import Sale

router = APIRouter(prefix="/api/finance", tags=["finance"])


class ExpenseIn(BaseModel):
    branch_id: int | None = None
    category: str
    amount: float
    note: str | None = None


@router.post("/expenses")
def add_expense(data: ExpenseIn,
                user: User = Depends(require("finance", "create")),
                db: Session = Depends(get_db)):
    e = Expense(tenant_id=user.tenant_id, **data.model_dump())
    db.add(e)
    db.commit()
    db.refresh(e)
    return {"id": e.id}


@router.get("/expenses")
def list_expenses(user: User = Depends(require("finance", "view")),
                  db: Session = Depends(get_db)):
    rows = (db.query(Expense)
            .filter(Expense.tenant_id == user.tenant_id)
            .order_by(Expense.created_at.desc()).limit(200).all())
    return [{"id": e.id, "category": e.category, "amount": e.amount,
             "note": e.note, "created_at": e.created_at} for e in rows]


@router.get("/summary")
def summary(user: User = Depends(require("finance", "view")),
            db: Session = Depends(get_db)):
    revenue = (db.query(func.coalesce(func.sum(Sale.total), 0))
               .filter(Sale.tenant_id == user.tenant_id).scalar() or 0)
    expenses = (db.query(func.coalesce(func.sum(Expense.amount), 0))
                .filter(Expense.tenant_id == user.tenant_id).scalar() or 0)
    return {"revenue": float(revenue), "expenses": float(expenses),
            "net": float(revenue) - float(expenses)}
'''

FILES["backend/app/api/audit.py"] = '''from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require
from app.models.user import User
from app.models.audit import AuditLog

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
def list_logs(limit: int = 100, action: str | None = None,
              entity: str | None = None,
              user: User = Depends(require("reports", "view")),
              db: Session = Depends(get_db)):
    q = db.query(AuditLog).filter(AuditLog.tenant_id == user.tenant_id)
    if action:
        q = q.filter(AuditLog.action == action)
    if entity:
        q = q.filter(AuditLog.entity == entity)
    rows = q.order_by(AuditLog.created_at.desc()).limit(limit).all()
    return [{"id": a.id, "user_id": a.user_id, "action": a.action,
             "entity": a.entity, "entity_id": a.entity_id,
             "payload": a.payload,
             "created_at": a.created_at} for a in rows]
'''

FILES["backend/app/api/intelligence.py"] = '''from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require
from app.models.user import User

router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])


@router.get("/profit")
def profit(user: User = Depends(require("intelligence", "view")),
           db: Session = Depends(get_db)):
    try:
        from app.intelligence.profit import real_profit
        return real_profit(db, user.tenant_id)
    except ImportError:
        return {"available": False, "reason": "Module not installed yet"}


@router.get("/leakage")
def leakage(user: User = Depends(require("intelligence", "view")),
            db: Session = Depends(get_db)):
    try:
        from app.intelligence.leakage import detect_leakage
        return detect_leakage(db, user.tenant_id)
    except ImportError:
        return {"total_leak": 0, "findings": []}


@router.get("/dead-stock")
def dead(user: User = Depends(require("intelligence", "view")),
         db: Session = Depends(get_db)):
    try:
        from app.intelligence.dead_stock_tiers import summary as dst_summary
        return dst_summary(db, user.tenant_id)
    except ImportError:
        return {"total_capital_locked": 0, "by_tier": {}}


@router.get("/forecast")
def forecast(user: User = Depends(require("intelligence", "view")),
             db: Session = Depends(get_db)):
    try:
        from app.intelligence.forecast import revenue_forecast
        return revenue_forecast(db, user.tenant_id)
    except ImportError:
        return {"available": False, "reason": "Module not installed yet"}


@router.get("/recommendations")
def recs(user: User = Depends(require("intelligence", "view")),
         db: Session = Depends(get_db)):
    try:
        from app.intelligence.optimization import generate as opt_generate
        return opt_generate(db, user.tenant_id)
    except ImportError:
        return {"summary": {"total_actions": 0}, "actions": []}
'''

FILES["backend/app/api/ai.py"] = '''from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require
from app.models.user import User

router = APIRouter(prefix="/api/ai", tags=["ai"])


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    branch_id: int | None = None


@router.post("/copilot")
def copilot(data: AskIn,
            user: User = Depends(require("ai", "use")),
            db: Session = Depends(get_db)):
    try:
        from app.ai.copilot import ask
        resp = ask(db, user.tenant_id, data.question, data.branch_id)
        return resp.to_dict()
    except ImportError:
        return {
            "answer_tj": "AI Copilot hali ornatilmagan.",
            "mode": "local", "intent": "empty", "used_sections": [],
        }
'''


def main():
    count = 0
    for path, content in FILES.items():
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        count += 1
        print(f"OK {path}")

    print(f"\nRound 2a: {count} ta fayl yaratildi")

    subprocess.run(["git", "add", "-A"], check=False)
    subprocess.run(["git", "commit", "-m", "Round 2a: Services + Schemas + API"], check=False)
    subprocess.run(["git", "push", "origin", "main"], check=False)
    print("GitHub'ga yuborildi")


if __name__ == "__main__":
    main()
