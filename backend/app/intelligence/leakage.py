from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.sale import Sale, SaleItem
from app.models.product import Product
from app.models.inventory_count import InventoryCount


def _excess_discount(db, tenant_id):
    row = db.query(
        func.coalesce(func.sum(Sale.total), 0),
        func.coalesce(func.sum(Sale.discount), 0),
    ).filter(Sale.tenant_id == tenant_id).first()
    revenue, discount = float(row[0] or 0), float(row[1] or 0)
    gross = revenue + discount
    if gross <= 0:
        return None
    pct = discount / gross * 100
    THRESHOLD = 8.0
    if pct <= THRESHOLD:
        return None
    excess = discount - gross * (THRESHOLD / 100)
    return {"kind": "excess_discount", "amount": round(excess, 2),
            "message_tj": f"Skidka ulushi {pct:.1f}% - meyordan yuqori. "
                          f"Ortiqcha: {excess:,.0f} TJS."}


def _below_cost(db, tenant_id):
    rows = (db.query(SaleItem, Product)
            .join(Product, Product.id == SaleItem.product_id)
            .filter(SaleItem.tenant_id == tenant_id,
                    SaleItem.price < Product.cost_price).all())
    loss = 0.0
    count = 0
    for si, p in rows:
        diff = (p.cost_price or 0) - (si.price or 0)
        if diff > 0:
            loss += diff * (si.quantity or 0)
            count += 1
    if count == 0:
        return None
    return {"kind": "below_cost_sales", "amount": round(loss, 2),
            "message_tj": f"{count} ta sotuv tan narxdan past. "
                          f"Real zarar: {loss:,.0f} TJS."}


def _inventory_variance(db, tenant_id, days=30):
    since = datetime.utcnow() - timedelta(days=days)
    rows = db.query(InventoryCount).filter(
        InventoryCount.tenant_id == tenant_id,
        InventoryCount.created_at >= since,
        InventoryCount.variance_value > 0,
    ).all()
    if not rows:
        return None
    loss = sum(r.variance_value for r in rows)
    return {"kind": "inventory_variance", "amount": round(loss, 2),
            "message_tj": f"{len(rows)} ta inventarizatsiyada "
                          f"{loss:,.0f} TJS zarar."}


def detect_leakage(db, tenant_id):
    findings = []
    for fn in (_excess_discount, _below_cost, _inventory_variance):
        r = fn(db, tenant_id)
        if r:
            findings.append(r)
    total = sum(f["amount"] for f in findings)
    return {"total_leak": round(total, 2), "findings": findings}
