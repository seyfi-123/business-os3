from enum import Enum
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.product import Product, Inventory
from app.models.sale import Sale, SaleItem
from app.models.purchase_history import PurchasePriceHistory


class CostingMethod(str, Enum):
    SNAPSHOT = "SNAPSHOT"
    MOVING_AVERAGE = "MOVING_AVERAGE"
    FIFO = "FIFO"
    LIFO = "LIFO"


def _purchase_layers(db, tenant_id, product_id, since=None, until=None):
    q = db.query(PurchasePriceHistory).filter(
        PurchasePriceHistory.tenant_id == tenant_id,
        PurchasePriceHistory.product_id == product_id,
    )
    if since is not None:
        q = q.filter(PurchasePriceHistory.created_at >= since)
    if until is not None:
        q = q.filter(PurchasePriceHistory.created_at <= until)
    return [
        {"qty": float(l.quantity or 0),
         "price": float(l.unit_price or 0),
         "t": l.created_at}
        for l in q.order_by(PurchasePriceHistory.created_at.asc(),
                            PurchasePriceHistory.id.asc()).all()
    ]


def _sold_rows(db, tenant_id, product_id, since=None, until=None,
               branch_id=None):
    q = (db.query(SaleItem, Sale)
         .join(Sale, Sale.id == SaleItem.sale_id)
         .filter(SaleItem.tenant_id == tenant_id,
                 SaleItem.product_id == product_id))
    if since is not None:
        q = q.filter(Sale.created_at >= since)
    if until is not None:
        q = q.filter(Sale.created_at <= until)
    if branch_id is not None:
        q = q.filter(Sale.branch_id == branch_id)
    return q.order_by(Sale.created_at.asc(), SaleItem.id.asc()).all()


def _total_sold_qty(db, tenant_id, product_id, since=None, until=None,
                    branch_id=None):
    q = (db.query(func.coalesce(func.sum(SaleItem.quantity), 0))
         .join(Sale, Sale.id == SaleItem.sale_id)
         .filter(SaleItem.tenant_id == tenant_id,
                 SaleItem.product_id == product_id))
    if since is not None:
        q = q.filter(Sale.created_at >= since)
    if until is not None:
        q = q.filter(Sale.created_at <= until)
    if branch_id is not None:
        q = q.filter(Sale.branch_id == branch_id)
    return float(q.scalar() or 0)


def _fallback_cost(db, product_id):
    p = db.query(Product).filter(Product.id == product_id).first()
    return float(p.cost_price or 0) if p else 0.0


def moving_average_cost(db, tenant_id, product_id, as_of=None):
    layers = _purchase_layers(db, tenant_id, product_id, until=as_of)
    total_qty = sum(l["qty"] for l in layers)
    if total_qty <= 0:
        return _fallback_cost(db, product_id)
    total_val = sum(l["qty"] * l["price"] for l in layers)
    return total_val / total_qty


def unit_cost_at(db, tenant_id, product_id, as_of=None,
                 method=CostingMethod.MOVING_AVERAGE):
    if method == CostingMethod.SNAPSHOT:
        raise ValueError("SNAPSHOT SaleItem.cost dan olinadi.")
    if method == CostingMethod.MOVING_AVERAGE:
        return moving_average_cost(db, tenant_id, product_id, as_of=as_of)
    layers = _purchase_layers(db, tenant_id, product_id, until=as_of)
    sold = _total_sold_qty(db, tenant_id, product_id, until=as_of)
    while sold > 0 and layers:
        idx = 0 if method == CostingMethod.FIFO else -1
        layer = layers[idx]
        take = min(sold, layer["qty"])
        layer["qty"] -= take
        sold -= take
        if layer["qty"] <= 0:
            layers.pop(idx)
    if not layers:
        return _fallback_cost(db, product_id)
    idx = 0 if method == CostingMethod.FIFO else -1
    return layers[idx]["price"]


def cogs_for_period(db, tenant_id, since, until,
                    method=CostingMethod.SNAPSHOT,
                    product_id=None, branch_id=None):
    if method == CostingMethod.SNAPSHOT:
        q = (db.query(
                func.coalesce(func.sum(SaleItem.cost * SaleItem.quantity), 0),
                func.coalesce(func.sum(SaleItem.quantity), 0),
                func.count(SaleItem.id))
             .join(Sale, Sale.id == SaleItem.sale_id)
             .filter(SaleItem.tenant_id == tenant_id,
                     Sale.created_at >= since,
                     Sale.created_at <= until))
        if product_id is not None:
            q = q.filter(SaleItem.product_id == product_id)
        if branch_id is not None:
            q = q.filter(Sale.branch_id == branch_id)
        cogs, qty, count = q.first()
        return {"method": method.value,
                "cogs": round(float(cogs or 0), 2),
                "quantity_sold": float(qty or 0),
                "sales_count": int(count or 0)}

    pid_q = (db.query(SaleItem.product_id)
             .join(Sale, Sale.id == SaleItem.sale_id)
             .filter(SaleItem.tenant_id == tenant_id,
                     Sale.created_at >= since,
                     Sale.created_at <= until))
    if product_id is not None:
        pid_q = pid_q.filter(SaleItem.product_id == product_id)
    if branch_id is not None:
        pid_q = pid_q.filter(Sale.branch_id == branch_id)
    product_ids = {r[0] for r in pid_q.distinct().all()}

    total_cogs = 0.0
    total_qty = 0.0
    total_sales = 0
    for pid in product_ids:
        if method == CostingMethod.MOVING_AVERAGE:
            p_cogs, p_qty, p_count = _moving_avg_for_product(
                db, tenant_id, pid, since, until, branch_id)
        else:
            p_cogs, p_qty, p_count = _fifo_lifo_for_product(
                db, tenant_id, pid, since, until, method, branch_id)
        total_cogs += p_cogs
        total_qty += p_qty
        total_sales += p_count

    return {"method": method.value,
            "cogs": round(total_cogs, 2),
            "quantity_sold": total_qty,
            "sales_count": total_sales}


def _moving_avg_for_product(db, tenant_id, product_id, since, until,
                            branch_id=None):
    rows = _sold_rows(db, tenant_id, product_id, since=since, until=until,
                      branch_id=branch_id)
    total_cogs = 0.0
    total_qty = 0.0
    for si, s in rows:
        unit = moving_average_cost(db, tenant_id, product_id,
                                   as_of=s.created_at)
        total_cogs += float(si.quantity or 0) * unit
        total_qty += float(si.quantity or 0)
    return total_cogs, total_qty, len(rows)


def _fifo_lifo_for_product(db, tenant_id, product_id, since, until,
                           method, branch_id=None):
    layers = _purchase_layers(db, tenant_id, product_id, until=until)
    rows = _sold_rows(db, tenant_id, product_id, until=until,
                      branch_id=branch_id)
    total_cogs = 0.0
    total_qty = 0.0
    count = 0
    for si, s in rows:
        qty_needed = float(si.quantity or 0)
        cogs_line = 0.0
        while qty_needed > 0 and layers:
            idx = 0 if method == CostingMethod.FIFO else -1
            layer = layers[idx]
            take = min(qty_needed, layer["qty"])
            cogs_line += take * layer["price"]
            layer["qty"] -= take
            qty_needed -= take
            if layer["qty"] <= 0:
                layers.pop(idx)
        if qty_needed > 0:
            cogs_line += qty_needed * _fallback_cost(db, product_id)
        if s.created_at >= since:
            total_cogs += cogs_line
            total_qty += float(si.quantity or 0)
            count += 1
    return total_cogs, total_qty, count


def stock_valuation(db, tenant_id, product_id=None, branch_id=None,
                    method=CostingMethod.MOVING_AVERAGE):
    if method == CostingMethod.SNAPSHOT:
        raise ValueError("SNAPSHOT qollanilmaydi.")
    q = db.query(Inventory).filter(Inventory.tenant_id == tenant_id)
    if product_id is not None:
        q = q.filter(Inventory.product_id == product_id)
    if branch_id is not None:
        q = q.filter(Inventory.branch_id == branch_id)
    rows = q.all()
    total_qty = 0.0
    total_value = 0.0
    by_product = {}
    for r in rows:
        by_product.setdefault(r.product_id, []).append(
            {"qty": float(r.quantity or 0)})
        total_qty += float(r.quantity or 0)
    if method == CostingMethod.MOVING_AVERAGE:
        for pid, items in by_product.items():
            unit = moving_average_cost(db, tenant_id, pid)
            for it in items:
                total_value += it["qty"] * unit
    else:
        for pid, items in by_product.items():
            qty = sum(it["qty"] for it in items)
            layers = _purchase_layers(db, tenant_id, pid)
            sold = _total_sold_qty(db, tenant_id, pid)
            while sold > 0 and layers:
                idx = 0 if method == CostingMethod.FIFO else -1
                layer = layers[idx]
                take = min(sold, layer["qty"])
                layer["qty"] -= take
                sold -= take
                if layer["qty"] <= 0:
                    layers.pop(idx)
            need = qty
            for layer in layers:
                if need <= 0:
                    break
                take = min(need, layer["qty"])
                total_value += take * layer["price"]
                need -= take
            if need > 0:
                total_value += need * _fallback_cost(db, pid)
    return {"method": method.value,
            "total_qty": round(total_qty, 2),
            "total_value": round(total_value, 2),
            "unit_cost_avg": round(total_value / total_qty, 2) if total_qty else 0.0}


def products_valuation(db, tenant_id, branch_id=None,
                       method=CostingMethod.MOVING_AVERAGE):
    if method == CostingMethod.SNAPSHOT:
        raise ValueError("SNAPSHOT qollanilmaydi.")
    q = (db.query(Inventory.product_id,
                  func.coalesce(func.sum(Inventory.quantity), 0).label("qty"))
         .filter(Inventory.tenant_id == tenant_id))
    if branch_id is not None:
        q = q.filter(Inventory.branch_id == branch_id)
    rows = q.group_by(Inventory.product_id).all()
    out = []
    for pid, qty in rows:
        qty = float(qty or 0)
        if qty <= 0:
            continue
        p = db.query(Product).filter(Product.id == pid).first()
        if not p:
            continue
        if method == CostingMethod.MOVING_AVERAGE:
            unit = moving_average_cost(db, tenant_id, pid)
        else:
            unit = unit_cost_at(db, tenant_id, pid, method=method)
        out.append({"product_id": pid, "sku": p.sku, "name": p.name,
                    "quantity": qty, "unit_cost": round(unit, 2),
                    "total_value": round(qty * unit, 2),
                    "method": method.value})
    out.sort(key=lambda x: x["total_value"], reverse=True)
    return out
