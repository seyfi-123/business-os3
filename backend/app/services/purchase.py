from collections import defaultdict
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func
from fastapi import HTTPException

from app.models.supplier import Supplier, Purchase, PurchaseItem
from app.models.product import Product, InventoryMovement
from app.models.purchase_history import PurchasePriceHistory
from app.models.audit import AuditLog
from app.core.validators import ensure_branch, ensure_supplier
from app.core.tx import transaction
from app.services.inventory import get_or_create_inventory


def weighted_avg_cost(db: Session, product_id: int, days: int = 90) -> float:
    since = datetime.utcnow() - timedelta(days=days)
    row = db.query(
        func.coalesce(func.sum(PurchasePriceHistory.total), 0),
        func.coalesce(func.sum(PurchasePriceHistory.quantity), 0),
    ).filter(
        PurchasePriceHistory.product_id == product_id,
        PurchasePriceHistory.created_at >= since,
    ).first()
    total_val, total_qty = float(row[0] or 0), float(row[1] or 0)
    if total_qty == 0:
        p = db.query(Product).filter(Product.id == product_id).first()
        return (p.cost_price or 0) if p else 0
    return total_val / total_qty


def _aggregate(items):
    acc = defaultdict(float)
    for it in items:
        key = (it["product_id"], float(it["price"]))
        acc[key] += float(it["quantity"])
    return [{"product_id": pid, "price": price, "quantity": qty}
            for (pid, price), qty in acc.items()]


def create_purchase(db: Session, tenant_id: int, branch_id: int,
                    supplier_id, items, paid: float,
                    user_id=None, ip=None) -> Purchase:
    if not items:
        raise HTTPException(400, "Xarid bosh")
    if paid < 0:
        raise HTTPException(400, "Tolangan summa manfiy")

    ensure_branch(db, tenant_id, branch_id)
    if supplier_id:
        ensure_supplier(db, tenant_id, supplier_id)

    items = _aggregate(items)
    pid_set = {it["product_id"] for it in items}
    found = db.query(Product).filter(
        Product.tenant_id == tenant_id, Product.id.in_(pid_set)
    ).all()
    if len(found) != len(pid_set):
        raise HTTPException(404, "Bazi mahsulotlar topilmadi")

    total = 0.0
    for it in items:
        if it["quantity"] <= 0 or it["price"] < 0:
            raise HTTPException(400, "Miqdor > 0, narx >= 0")
        total += it["quantity"] * it["price"]

    if paid > total:
        raise HTTPException(400, "Tolov jamidan katta")

    with transaction(db):
        purchase = Purchase(
            tenant_id=tenant_id, branch_id=branch_id,
            supplier_id=supplier_id, total=total, paid=paid,
        )
        db.add(purchase)
        db.flush()

        for it in items:
            db.add(PurchaseItem(
                tenant_id=tenant_id, purchase_id=purchase.id,
                product_id=it["product_id"],
                quantity=it["quantity"], price=it["price"],
            ))
            db.add(PurchasePriceHistory(
                tenant_id=tenant_id, product_id=it["product_id"],
                supplier_id=supplier_id, purchase_id=purchase.id,
                quantity=it["quantity"], unit_price=it["price"],
                total=it["quantity"] * it["price"],
            ))
            inv = get_or_create_inventory(db, tenant_id, branch_id,
                                          it["product_id"], lock=True)
            inv.quantity += it["quantity"]
            db.add(InventoryMovement(
                tenant_id=tenant_id, branch_id=branch_id,
                product_id=it["product_id"], delta=it["quantity"],
                reason="PURCHASE", ref_id=purchase.id, user_id=user_id,
            ))

        for it in items:
            db.flush()
            p = db.query(Product).filter(
                Product.id == it["product_id"]).first()
            p.cost_price = weighted_avg_cost(db, p.id)

        debt = total - paid
        if debt > 0 and supplier_id:
            s = db.query(Supplier).filter(Supplier.id == supplier_id).first()
            s.debt = (s.debt or 0) + debt

        db.add(AuditLog(
            tenant_id=tenant_id, user_id=user_id, action="CREATE",
            entity="Purchase", entity_id=purchase.id,
            payload={"total": total, "paid": paid, "items": len(items)},
            ip=ip,
        ))

    db.refresh(purchase)
    return purchase
