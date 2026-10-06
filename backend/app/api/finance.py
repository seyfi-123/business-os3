from fastapi import APIRouter, Depends
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
