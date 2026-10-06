from fastapi import APIRouter, Depends
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
