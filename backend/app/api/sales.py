from fastapi import APIRouter, Depends, HTTPException, Request
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
