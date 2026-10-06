from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.deps import current_user, require
from app.models.user import User
from app.billing.plans import list_plans, PLANS
from app.billing.schemas import SubscriptionOut, SubscribeIn, InvoiceOut, UsageOut
from app.billing.service import (get_or_create_subscription, change_plan,
                                  cancel_subscription, mark_invoice_paid,
                                  usage_snapshot, days_remaining)
from app.billing.models import Subscription, Invoice

router = APIRouter(prefix="/api/billing", tags=["billing"])


def _sub_d(sub):
    plan = PLANS.get(sub.plan_code)
    return {"id": sub.id, "tenant_id": sub.tenant_id, "plan_code": sub.plan_code,
            "plan": plan.to_dict() if plan else None, "status": sub.status,
            "started_at": sub.started_at, "trial_end": sub.trial_end,
            "current_period_start": sub.current_period_start,
            "current_period_end": sub.current_period_end,
            "cancelled_at": sub.cancelled_at, "auto_renew": sub.auto_renew,
            "days_remaining": days_remaining(sub)}


def _inv_d(inv):
    return {"id": inv.id, "number": inv.number, "tenant_id": inv.tenant_id,
            "plan_code": inv.plan_code, "amount": inv.amount,
            "currency": inv.currency, "status": inv.status,
            "period_start": inv.period_start, "period_end": inv.period_end,
            "issued_at": inv.issued_at, "due_at": inv.due_at,
            "paid_at": inv.paid_at, "payment_method": inv.payment_method}


@router.get("/plans")
def plans():
    return list_plans()


@router.get("/subscription", response_model=SubscriptionOut)
def my_sub(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _sub_d(get_or_create_subscription(db, user.tenant_id))


@router.post("/subscribe", response_model=SubscriptionOut)
def subscribe(data: SubscribeIn, user: User = Depends(require("finance", "edit")),
              db: Session = Depends(get_db)):
    return _sub_d(change_plan(db, user.tenant_id, data.plan_code))


@router.post("/cancel", response_model=SubscriptionOut)
def cancel(user: User = Depends(require("finance", "edit")),
           db: Session = Depends(get_db)):
    return _sub_d(cancel_subscription(db, user.tenant_id))


@router.get("/usage", response_model=UsageOut)
def usage(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return usage_snapshot(db, user.tenant_id)


@router.get("/invoices", response_model=list[InvoiceOut])
def invoices(user: User = Depends(require("finance", "view")),
             db: Session = Depends(get_db)):
    rows = (db.query(Invoice).filter(Invoice.tenant_id == user.tenant_id)
            .order_by(Invoice.issued_at.desc()).limit(100).all())
    return [_inv_d(i) for i in rows]


@router.post("/invoices/{invoice_id}/pay", response_model=InvoiceOut)
def pay_demo(invoice_id: int, method: str = "BANK",
             user: User = Depends(require("finance", "edit")),
             db: Session = Depends(get_db)):
    return _inv_d(mark_invoice_paid(db, invoice_id, user.tenant_id,
                                     method=method, via="DEMO",
                                     actor_user_id=user.id))


@router.post("/invoices/{invoice_id}/mark-paid-manual", response_model=InvoiceOut)
def manual(invoice_id: int, method: str = "BANK",
           user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role not in ("OWNER", "ADMIN"):
        raise HTTPException(403, "Faqat OWNER/ADMIN")
    return _inv_d(mark_invoice_paid(db, invoice_id, user.tenant_id,
                                     method=method, via="MANUAL",
                                     actor_user_id=user.id))


@router.post("/webhook/provider")
def webhook(payload: dict, db: Session = Depends(get_db)):
    raise HTTPException(501, "Provider webhook sozlanmagan")
