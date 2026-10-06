from fastapi import APIRouter, Depends, HTTPException
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
