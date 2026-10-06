from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from fastapi import HTTPException

from app.models.company import Branch
from app.models.user import User
from app.models.product import Product
from app.models.sale import Sale
from app.models.supplier import Purchase
from app.models.audit import AuditLog
from app.billing.models import Subscription, Invoice
from app.billing.plans import PLANS, DEFAULT_PLAN, get_plan
from app.billing.config import billing_settings
from app.core.tx import transaction


def get_subscription(db, tenant_id):
    return (db.query(Subscription).filter(Subscription.tenant_id == tenant_id)
            .order_by(Subscription.id.desc()).first())


def get_active_subscription(db, tenant_id):
    now = datetime.utcnow()
    s = (db.query(Subscription).filter(
        Subscription.tenant_id == tenant_id,
        Subscription.status.in_(["TRIAL", "ACTIVE", "PAST_DUE"]))
         .order_by(Subscription.id.desc()).first())
    if not s:
        return None
    if s.status == "TRIAL" and s.trial_end and s.trial_end <= now:
        return None
    return s


def has_ever_had_trial(db, tenant_id):
    return (db.query(func.count(Subscription.id)).filter(
        Subscription.tenant_id == tenant_id,
        Subscription.trial_end.isnot(None)).scalar() or 0) > 0


def _insert_trial(db, tid, plan_code):
    plan = get_plan(plan_code)
    now = datetime.utcnow()
    sub = Subscription(tenant_id=tid, plan_code=plan.code, status="TRIAL",
                       started_at=now,
                       trial_end=now + timedelta(days=plan.trial_days),
                       current_period_start=now,
                       current_period_end=now + timedelta(days=plan.billing_period_days),
                       auto_renew=True)
    db.add(sub); db.flush(); db.refresh(sub)
    return sub


def get_or_create_subscription(db, tenant_id, plan_code=None):
    existing = get_active_subscription(db, tenant_id)
    if existing:
        return existing
    with transaction(db):
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": tenant_id})
        existing = get_active_subscription(db, tenant_id)
        if existing:
            return existing
        latest = (db.query(Subscription).filter(Subscription.tenant_id == tenant_id)
                  .order_by(Subscription.id.desc()).first())
        if latest is None:
            return _insert_trial(db, tenant_id, plan_code or DEFAULT_PLAN)
        return latest


def create_trial_subscription(db, tenant_id, plan_code=DEFAULT_PLAN):
    if has_ever_had_trial(db, tenant_id):
        raise HTTPException(400, "Bu tenant allaqachon TRIAL olgan")
    with transaction(db):
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": tenant_id})
        if has_ever_had_trial(db, tenant_id):
            raise HTTPException(400, "TRIAL allaqachon olingan")
        return _insert_trial(db, tenant_id, plan_code)


def _next_inv_num(db, tid):
    seq = db.execute(text("SELECT nextval('invoice_number_seq')")).scalar()
    return f"INV-{datetime.utcnow().year}-{tid:04d}-{int(seq):05d}"


def _issue_inv(db, sub, plan, now):
    inv = Invoice(tenant_id=sub.tenant_id, subscription_id=sub.id,
                  number=_next_inv_num(db, sub.tenant_id),
                  plan_code=plan.code, amount=plan.price_tjs, currency="TJS",
                  status="SENT", period_start=now,
                  period_end=now + timedelta(days=plan.billing_period_days),
                  due_at=now + timedelta(days=7))
    db.add(inv); db.flush()
    return inv


def change_plan(db, tenant_id, new_plan_code):
    if new_plan_code not in PLANS:
        raise HTTPException(400, f"Nomahlum plan: {new_plan_code}")
    sub = get_subscription(db, tenant_id)
    if not sub:
        return create_trial_subscription(db, tenant_id, new_plan_code)
    plan = get_plan(new_plan_code)
    now = datetime.utcnow()
    with transaction(db):
        sub.plan_code = new_plan_code
        sub.status = "ACTIVE"
        sub.current_period_start = now
        sub.current_period_end = now + timedelta(days=plan.billing_period_days)
        sub.cancelled_at = None
        if plan.price_tjs > 0:
            _issue_inv(db, sub, plan, now)
    db.refresh(sub)
    return sub


def cancel_subscription(db, tenant_id):
    sub = get_active_subscription(db, tenant_id)
    if not sub:
        raise HTTPException(404, "Faol obuna topilmadi")
    with transaction(db):
        sub.status = "CANCELLED"
        sub.cancelled_at = datetime.utcnow()
        sub.auto_renew = False
    db.refresh(sub)
    return sub


def mark_invoice_paid(db, invoice_id, tenant_id, method="BANK", *,
                      via, actor_user_id=None):
    if via == "DEMO" and not billing_settings.demo_enabled:
        raise HTTPException(403, "Demo payment o'chirilgan")
    inv = db.query(Invoice).filter(Invoice.id == invoice_id,
                                    Invoice.tenant_id == tenant_id).first()
    if not inv:
        raise HTTPException(404, "Invoice topilmadi")
    if inv.status == "PAID":
        raise HTTPException(400, "Allaqachon to'langan")
    with transaction(db):
        inv.status = "PAID"
        inv.paid_at = datetime.utcnow()
        inv.payment_method = method
        inv.note = (inv.note or "") + f" | via={via}"
        if actor_user_id:
            inv.note += f" actor={actor_user_id}"
        sub = db.query(Subscription).filter(Subscription.id == inv.subscription_id).first()
        if sub:
            sub.status = "ACTIVE"
        if via == "MANUAL":
            db.add(AuditLog(tenant_id=tenant_id, user_id=actor_user_id,
                            action="INVOICE_MANUAL_PAID", entity="Invoice",
                            entity_id=invoice_id,
                            payload={"method": method, "amount": inv.amount}))
    db.refresh(inv)
    return inv


def has_feature(db, tenant_id, feature):
    sub = get_active_subscription(db, tenant_id)
    if not sub:
        return False
    plan = PLANS.get(sub.plan_code)
    return bool(plan and plan.features.get(feature, False))


def within_limit(db, tenant_id, limit_name, current):
    sub = get_active_subscription(db, tenant_id)
    if not sub:
        return False
    plan = PLANS.get(sub.plan_code)
    if not plan:
        return False
    limit = plan.limits.get(limit_name)
    return True if limit is None else current < limit


def usage_snapshot(db, tenant_id):
    sub = get_active_subscription(db, tenant_id)
    plan = PLANS.get(sub.plan_code) if sub else None
    now = datetime.utcnow()
    ms = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    br = db.query(func.count(Branch.id)).filter(Branch.tenant_id == tenant_id).scalar() or 0
    emp = db.query(func.count(User.id)).filter(User.tenant_id == tenant_id).scalar() or 0
    pr = db.query(func.count(Product.id)).filter(Product.tenant_id == tenant_id,
                                                  Product.active.is_(True)).scalar() or 0
    inv = db.query(func.count(Sale.id)).filter(Sale.tenant_id == tenant_id,
                                                Sale.created_at >= ms).scalar() or 0
    pur = db.query(func.count(Purchase.id)).filter(Purchase.tenant_id == tenant_id,
                                                    Purchase.created_at >= ms).scalar() or 0
    items = [
        {"name": "branches", "current": br, "limit": plan.limits["branches"] if plan else None},
        {"name": "employees", "current": emp, "limit": plan.limits["employees"] if plan else None},
        {"name": "products", "current": pr, "limit": plan.limits["products"] if plan else None},
        {"name": "sales_this_month", "current": inv, "limit": plan.limits["invoices_per_month"] if plan else None},
        {"name": "purchases_this_month", "current": pur, "limit": plan.limits["purchases_per_month"] if plan else None}]
    for it in items:
        it["used_pct"] = None if it["limit"] is None else round(it["current"] / it["limit"] * 100, 1) if it["limit"] else 0.0
    return {"plan_code": sub.plan_code if sub else None,
            "status": sub.status if sub else None, "items": items}


def days_remaining(sub):
    now = datetime.utcnow()
    if sub.status == "TRIAL" and sub.trial_end:
        return max(0, (sub.trial_end - now).days)
    if sub.status in ("ACTIVE", "PAST_DUE"):
        return max(0, (sub.current_period_end - now).days)
    return None
