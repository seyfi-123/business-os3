from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.product import Product, Inventory
from app.models.sale import SaleItem, Sale


def dead_stock(db, tenant_id, days=60):
    since = datetime.utcnow() - timedelta(days=days)
    sold_rows = (db.query(SaleItem.product_id)
                 .join(Sale, Sale.id == SaleItem.sale_id)
                 .filter(SaleItem.tenant_id == tenant_id,
                         Sale.created_at >= since)
                 .distinct().all())
    sold_ids = {r[0] for r in sold_rows}
    items = []
    total_locked = 0.0
    rows = (db.query(Product, func.coalesce(func.sum(Inventory.quantity), 0))
            .outerjoin(Inventory, Inventory.product_id == Product.id)
            .filter(Product.tenant_id == tenant_id)
            .group_by(Product.id).all())
    for p, qty in rows:
        if qty <= 0 or p.id in sold_ids:
            continue
        locked = qty * (p.cost_price or 0)
        total_locked += locked
        items.append({"product_id": p.id, "sku": p.sku, "name": p.name,
                      "quantity": qty, "locked_capital": round(locked, 2),
                      "recommendation_tj": f"{days} kundan beri sotilmagan."})
    items.sort(key=lambda x: x["locked_capital"], reverse=True)
    return {"capital_locked": round(total_locked, 2),
            "items": items[:50], "days": days}
