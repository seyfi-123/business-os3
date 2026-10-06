from fastapi import APIRouter, Depends, HTTPException, Request
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
