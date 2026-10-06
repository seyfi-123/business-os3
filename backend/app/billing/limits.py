from datetime import datetime
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.database import get_db
from app.core.deps import current_user
from app.models.user import User
from app.models.company import Branch
from app.models.product import Product
from app.models.sale import Sale
from app.models.supplier import Purchase
from app.billing.service import has_feature, within_limit, get_active_subscription
from app.billing.plans import PLANS


def require_feature(feature):
    def checker(user: User = Depends(current_user), db: Session = Depends(get_db)):
        if not has_feature(db, user.tenant_id, feature):
            raise HTTPException(402, f"Tarifingizda bu funksiya yoq: {feature}")
        return user
    return checker


def _c_branches(db, tid):
    return db.query(func.count(Branch.id)).filter(Branch.tenant_id == tid).scalar() or 0


def _c_employees(db, tid):
    return db.query(func.count(User.id)).filter(User.tenant_id == tid).scalar() or 0


def _c_products(db, tid):
    return db.query(func.count(Product.id)).filter(
        Product.tenant_id == tid, Product.active.is_(True)).scalar() or 0


def _c_sales(db, tid):
    ms = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return db.query(func.count(Sale.id)).filter(Sale.tenant_id == tid,
                                                 Sale.created_at >= ms).scalar() or 0


def _c_purch(db, tid):
    ms = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return db.query(func.count(Purchase.id)).filter(Purchase.tenant_id == tid,
                                                     Purchase.created_at >= ms).scalar() or 0


LIMIT_GETTERS = {"branches": _c_branches, "employees": _c_employees,
                 "products": _c_products, "invoices_per_month": _c_sales,
                 "purchases_per_month": _c_purch}


def enforce_limit(limit_name):
    if limit_name not in LIMIT_GETTERS:
        raise ValueError(f"Nomahlum limit: {limit_name}")
    def checker(user: User = Depends(current_user), db: Session = Depends(get_db)):
        cur = LIMIT_GETTERS[limit_name](db, user.tenant_id)
        if not within_limit(db, user.tenant_id, limit_name, cur):
            sub = get_active_subscription(db, user.tenant_id)
            plan = PLANS.get(sub.plan_code) if sub else None
            lim = plan.limits.get(limit_name) if plan else None
            raise HTTPException(402, f"Limit tugadi: {limit_name} ({cur}/{lim})")
        return user
    return checker
