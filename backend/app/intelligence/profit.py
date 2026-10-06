from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.sale import Sale, SaleItem
from app.models.finance import Expense
from app.models.company import Branch
from app.models.product import Product
from app.intelligence.costing import cogs_for_period, CostingMethod


def real_profit(db, tenant_id, since=None, until=None,
                branch_id=None, costing=CostingMethod.SNAPSHOT):
    q_sales = db.query(
        func.coalesce(func.sum(Sale.total), 0),
        func.count(Sale.id),
    ).filter(Sale.tenant_id == tenant_id)
    if since:
        q_sales = q_sales.filter(Sale.created_at >= since)
    if until:
        q_sales = q_sales.filter(Sale.created_at <= until)
    if branch_id:
        q_sales = q_sales.filter(Sale.branch_id == branch_id)
    revenue, sales_count = q_sales.first()
    revenue = float(revenue or 0)

    effective_since = since or datetime(1970, 1, 1)
    effective_until = until or datetime.utcnow()
    cogs_result = cogs_for_period(
        db, tenant_id, effective_since, effective_until,
        method=costing, branch_id=branch_id)
    cogs = float(cogs_result["cogs"])

    q_exp = db.query(func.coalesce(func.sum(Expense.amount), 0))\
        .filter(Expense.tenant_id == tenant_id)
    if since:
        q_exp = q_exp.filter(Expense.created_at >= since)
    if until:
        q_exp = q_exp.filter(Expense.created_at <= until)
    if branch_id:
        q_exp = q_exp.filter(Expense.branch_id == branch_id)
    expenses = float(q_exp.scalar() or 0)

    gross = revenue - cogs
    net = gross - expenses
    return {
        "costing_method": costing.value,
        "revenue": round(revenue, 2),
        "sales_count": int(sales_count or 0),
        "cogs": round(cogs, 2),
        "gross_profit": round(gross, 2),
        "expenses": round(expenses, 2),
        "net_profit": round(net, 2),
        "gross_margin_pct": round((gross / revenue * 100) if revenue else 0, 2),
        "net_margin_pct": round((net / revenue * 100) if revenue else 0, 2),
    }


def profit_by_branch(db, tenant_id, since=None, until=None,
                     costing=CostingMethod.SNAPSHOT):
    out = []
    for b in db.query(Branch).filter(Branch.tenant_id == tenant_id).all():
        p = real_profit(db, tenant_id, since, until,
                        branch_id=b.id, costing=costing)
        out.append({"branch_id": b.id, "branch_name": b.name, **p})
    out.sort(key=lambda x: x["net_profit"], reverse=True)
    return out


def profit_by_product(db, tenant_id, since=None, until=None,
                      branch_id=None, limit=50):
    q = (db.query(
            SaleItem.product_id,
            func.sum(SaleItem.quantity).label("qty"),
            func.sum(SaleItem.price * SaleItem.quantity).label("revenue"),
            func.sum(SaleItem.cost * SaleItem.quantity).label("cogs"))
         .join(Sale, Sale.id == SaleItem.sale_id)
         .filter(SaleItem.tenant_id == tenant_id))
    if since:
        q = q.filter(Sale.created_at >= since)
    if until:
        q = q.filter(Sale.created_at <= until)
    if branch_id:
        q = q.filter(Sale.branch_id == branch_id)
    rows = q.group_by(SaleItem.product_id).all()
    products = {p.id: p for p in db.query(Product).filter(
        Product.tenant_id == tenant_id).all()}
    result = []
    for pid, qty, rev, cogs in rows:
        rev = float(rev or 0)
        cogs = float(cogs or 0)
        profit = rev - cogs
        p = products.get(pid)
        result.append({
            "product_id": pid,
            "name": p.name if p else "?",
            "sku": p.sku if p else None,
            "quantity": float(qty or 0),
            "revenue": round(rev, 2),
            "cogs": round(cogs, 2),
            "profit": round(profit, 2),
            "margin_pct": round((profit / rev * 100) if rev else 0, 2),
        })
    result.sort(key=lambda x: x["profit"], reverse=True)
    return result[:limit]
