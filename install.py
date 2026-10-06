"""
Business OS — Auto installer
Round 2b: Intelligence asos (costing, profit, leakage)
"""
import os
import subprocess

FILES = {}

FILES["backend/app/intelligence/__init__.py"] = ""

FILES["backend/app/intelligence/costing.py"] = '''from enum import Enum
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
'''

FILES["backend/app/intelligence/profit.py"] = '''from datetime import datetime
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

    q_exp = db.query(func.coalesce(func.sum(Expense.amount), 0))\\
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
'''

FILES["backend/app/intelligence/leakage.py"] = '''from datetime import datetime, timedelta
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
'''

FILES["backend/app/intelligence/dead_stock.py"] = '''from datetime import datetime, timedelta
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
'''


def main():
    count = 0
    for path, content in FILES.items():
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        count += 1
        print(f"OK {path}")
    print(f"\nRound 2b: {count} ta fayl")
    subprocess.run(["git", "add", "-A"], check=False)
    subprocess.run(["git", "commit", "-m", "Round 2b: Intelligence base"], check=False)
    subprocess.run(["git", "push", "origin", "main"], check=False)
    print("GitHub'ga yuborildi")


if __name__ == "__main__":
    main()
