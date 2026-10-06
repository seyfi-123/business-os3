from fastapi import APIRouter, Depends, HTTPException
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
