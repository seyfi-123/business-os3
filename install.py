"""
Business OS — Auto installer
Round 2c: dead_stock_tiers + supplier_anomaly
"""
import os
import subprocess

FILES = {}

FILES["backend/app/intelligence/dead_stock_tiers.py"] = '''from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.product import Product, Inventory
from app.models.sale import Sale, SaleItem
from app.intelligence.costing import (
    products_valuation, _purchase_layers, _total_sold_qty,
    CostingMethod,
)


@dataclass
class TiersConfig:
    dead_days: int = 90
    slow_days: int = 45
    risk_days: int = 21
    slow_velocity: float = 0.5
    velocity_window: int = 30
    trend_drop_pct: float = 40.0
    min_capital: float = 0.0
    chunk_size: int = 500

    def to_dict(self):
        return asdict(self)


def _chunks(seq, size):
    seq = list(seq)
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _last_sale_map(db, tenant_id, product_ids, branch_id=None):
    out = {}
    for chunk in _chunks(product_ids, 500):
        q = (db.query(SaleItem.product_id, func.max(Sale.created_at))
             .join(Sale, Sale.id == SaleItem.sale_id)
             .filter(SaleItem.tenant_id == tenant_id,
                     SaleItem.product_id.in_(chunk)))
        if branch_id is not None:
            q = q.filter(Sale.branch_id == branch_id)
        for pid, t in q.group_by(SaleItem.product_id).all():
            if t is not None:
                out[pid] = t
    return out


def _qty_in_window(db, tenant_id, product_ids, start, end, branch_id=None):
    out = {}
    for chunk in _chunks(product_ids, 500):
        q = (db.query(SaleItem.product_id, func.sum(SaleItem.quantity))
             .join(Sale, Sale.id == SaleItem.sale_id)
             .filter(SaleItem.tenant_id == tenant_id,
                     SaleItem.product_id.in_(chunk),
                     Sale.created_at >= start,
                     Sale.created_at < end))
        if branch_id is not None:
            q = q.filter(Sale.branch_id == branch_id)
        for pid, qty in q.group_by(SaleItem.product_id).all():
            out[pid] = float(qty or 0)
    return out


def _stock_age_days(db, tenant_id, product_id, as_of=None):
    now = as_of or datetime.utcnow()
    layers = _purchase_layers(db, tenant_id, product_id)
    if not layers:
        return None
    sold = _total_sold_qty(db, tenant_id, product_id)
    while sold > 0 and layers:
        layer = layers[0]
        take = min(sold, layer["qty"])
        layer["qty"] -= take
        sold -= take
        if layer["qty"] <= 0:
            layers.pop(0)
    remaining = [(l["qty"], l["t"]) for l in layers if l["qty"] > 0]
    if not remaining:
        return None
    total_qty = sum(q for q, _ in remaining)
    if total_qty <= 0:
        return None
    weighted = sum(q * (now - t).days for q, t in remaining) / total_qty
    return round(weighted, 1)


def _classify_one(quantity, capital_locked, last_sale_days,
                  velocity_recent, velocity_prev, stock_age_days, cfg):
    if last_sale_days is not None and last_sale_days >= cfg.dead_days:
        return ("DEAD", "DISCOUNT",
                f"{int(last_sale_days)} kundan beri sotilmagan")
    if last_sale_days is None and (stock_age_days or 0) >= cfg.dead_days:
        return ("DEAD", "DISCOUNT",
                f"Hech qachon sotilmagan, zaxira yoshi {int(stock_age_days or 0)} kun")

    if last_sale_days is not None and last_sale_days >= cfg.risk_days:
        if last_sale_days < cfg.slow_days and velocity_recent < cfg.slow_velocity * 1.5:
            return ("AT-RISK", "MONITOR",
                    f"Oxirgi sotuv {int(last_sale_days)} kun oldin, "
                    f"sekinlashmoqda ({velocity_recent:.2f} dona/kun)")

    if velocity_recent < cfg.slow_velocity:
        if last_sale_days is not None and last_sale_days >= cfg.slow_days:
            return ("SLOW", "PROMOTE",
                    f"Oxirgi sotuv {int(last_sale_days)} kun oldin, "
                    f"tezlik {velocity_recent:.2f} dona/kun")
        if velocity_recent == 0:
            return ("SLOW", "PROMOTE",
                    f"Oxirgi {cfg.velocity_window} kunda sotuv yoq")
        return ("SLOW", "PROMOTE",
                f"Sotuv tezligi past: {velocity_recent:.2f} dona/kun")

    if velocity_prev > 0:
        drop_pct = (velocity_prev - velocity_recent) / velocity_prev * 100
        if drop_pct >= cfg.trend_drop_pct:
            return ("AT-RISK", "MONITOR",
                    f"Talab {drop_pct:.0f}% pasaydi")
    return ("NORMAL", "RETAIN", "")


def classify(db, tenant_id, branch_id=None, cfg=None,
             include_normal=False,
             costing=CostingMethod.MOVING_AVERAGE):
    cfg = cfg or TiersConfig()
    now = datetime.utcnow()
    valuation = products_valuation(db, tenant_id, branch_id=branch_id,
                                   method=costing)
    valuation = {v["product_id"]: v for v in valuation}
    if not valuation:
        return _empty_result(cfg)

    product_ids = list(valuation.keys())
    last_sale_map = _last_sale_map(db, tenant_id, product_ids, branch_id)
    recent_start = now - timedelta(days=cfg.velocity_window)
    prev_start = now - timedelta(days=2 * cfg.velocity_window)
    recent_qty = _qty_in_window(db, tenant_id, product_ids,
                                recent_start, now, branch_id)
    prev_qty = _qty_in_window(db, tenant_id, product_ids,
                              prev_start, recent_start, branch_id)

    items = []
    tier_buckets = {
        "DEAD": {"count": 0, "capital_locked": 0.0, "quantity": 0.0},
        "SLOW": {"count": 0, "capital_locked": 0.0, "quantity": 0.0},
        "AT-RISK": {"count": 0, "capital_locked": 0.0, "quantity": 0.0},
        "NORMAL": {"count": 0, "capital_locked": 0.0, "quantity": 0.0},
    }
    total_locked = 0.0

    for pid, val in valuation.items():
        capital_locked = float(val["total_value"])
        quantity = float(val["quantity"])
        if capital_locked < cfg.min_capital:
            continue
        total_locked += capital_locked
        last_t = last_sale_map.get(pid)
        last_sale_days = (now - last_t).days if last_t else None
        v_recent = recent_qty.get(pid, 0.0) / cfg.velocity_window
        v_prev = prev_qty.get(pid, 0.0) / cfg.velocity_window
        stock_age = _stock_age_days(db, tenant_id, pid)

        tier, action, reason = _classify_one(
            quantity, capital_locked, last_sale_days,
            v_recent, v_prev, stock_age, cfg)
        tier_buckets[tier]["count"] += 1
        tier_buckets[tier]["capital_locked"] += capital_locked
        tier_buckets[tier]["quantity"] += quantity

        if tier == "NORMAL" and not include_normal:
            continue
        items.append({
            "product_id": pid,
            "sku": val.get("sku"),
            "name": val.get("name"),
            "quantity": round(quantity, 2),
            "unit_cost": val.get("unit_cost"),
            "capital_locked": round(capital_locked, 2),
            "last_sale_days": int(last_sale_days) if last_sale_days is not None else None,
            "velocity": round(v_recent, 3),
            "velocity_prev": round(v_prev, 3),
            "stock_age_days": stock_age,
            "tier": tier,
            "action": action,
            "reason_tj": reason,
            "expiry_status": "unavailable",
        })

    items.sort(key=lambda x: x["capital_locked"], reverse=True)
    return {
        "config": cfg.to_dict(),
        "branch_id": branch_id,
        "costing_method": costing.value,
        "generated_at": now.isoformat(),
        "summary": {
            "total_products": len(valuation),
            "total_capital_locked": round(total_locked, 2),
            "by_tier": {k: {"count": v["count"],
                            "capital_locked": round(v["capital_locked"], 2),
                            "quantity": round(v["quantity"], 2)}
                        for k, v in tier_buckets.items()},
        },
        "items": items,
    }


def summary(db, tenant_id, branch_id=None, cfg=None,
            costing=CostingMethod.MOVING_AVERAGE):
    return classify(db, tenant_id, branch_id, cfg,
                    include_normal=False, costing=costing)["summary"]


def by_tier(db, tenant_id, tier, branch_id=None, cfg=None,
            costing=CostingMethod.MOVING_AVERAGE):
    if tier not in ("DEAD", "SLOW", "AT-RISK", "NORMAL"):
        raise ValueError(f"Notogri tier: {tier}")
    result = classify(db, tenant_id, branch_id, cfg,
                      include_normal=(tier == "NORMAL"), costing=costing)
    return [i for i in result["items"] if i["tier"] == tier]


def top_locked(db, tenant_id, limit=20, branch_id=None, cfg=None,
               costing=CostingMethod.MOVING_AVERAGE):
    result = classify(db, tenant_id, branch_id, cfg,
                      include_normal=False, costing=costing)
    return result["items"][:limit]


def _empty_result(cfg):
    return {
        "config": cfg.to_dict(),
        "branch_id": None,
        "costing_method": None,
        "generated_at": datetime.utcnow().isoformat(),
        "summary": {
            "total_products": 0,
            "total_capital_locked": 0.0,
            "by_tier": {
                "DEAD": {"count": 0, "capital_locked": 0.0, "quantity": 0.0},
                "SLOW": {"count": 0, "capital_locked": 0.0, "quantity": 0.0},
                "AT-RISK": {"count": 0, "capital_locked": 0.0, "quantity": 0.0},
                "NORMAL": {"count": 0, "capital_locked": 0.0, "quantity": 0.0},
            },
        },
        "items": [],
    }
'''

FILES["backend/app/intelligence/supplier_anomaly.py"] = '''from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from statistics import median
from sqlalchemy.orm import Session

from app.models.product import Product
from app.models.supplier import Supplier
from app.models.purchase_history import PurchasePriceHistory


@dataclass
class AnomalyConfig:
    window_days: int = 180
    min_history: int = 3
    pct_jump: float = 15.0
    z_threshold: float = 3.5
    supplier_gap_pct: float = 10.0
    trend_min_points: int = 3
    trend_pct: float = 10.0

    def to_dict(self):
        return asdict(self)


def _chunks(seq, size):
    seq = list(seq)
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _load_history(db, tenant_id, since, product_ids=None):
    if product_ids is None:
        return (db.query(PurchasePriceHistory)
                .filter(PurchasePriceHistory.tenant_id == tenant_id,
                        PurchasePriceHistory.created_at >= since)
                .order_by(PurchasePriceHistory.created_at.asc(),
                          PurchasePriceHistory.id.asc()).all())
    rows = []
    for chunk in _chunks(product_ids, 500):
        q = (db.query(PurchasePriceHistory)
             .filter(PurchasePriceHistory.tenant_id == tenant_id,
                     PurchasePriceHistory.created_at >= since,
                     PurchasePriceHistory.product_id.in_(chunk))
             .order_by(PurchasePriceHistory.created_at.asc(),
                       PurchasePriceHistory.id.asc()))
        rows.extend(q.all())
    return rows


def _robust_z(values):
    if len(values) < 3:
        return [0.0] * len(values)
    med = median(values)
    abs_dev = [abs(v - med) for v in values]
    mad = median(abs_dev)
    if mad == 0:
        return [0.0] * len(values)
    scale = 1.4826 * mad
    return [(v - med) / scale for v in values]


def _pct_change(prev, curr):
    if prev <= 0:
        return 0.0
    return (curr - prev) / prev * 100.0


def _detect_jumps(entries, cfg):
    out = []
    for i in range(1, len(entries)):
        prev, curr = entries[i - 1], entries[i]
        pct = _pct_change(float(prev.unit_price or 0),
                          float(curr.unit_price or 0))
        if pct >= cfg.pct_jump:
            qty = float(curr.quantity or 0)
            impact = (float(curr.unit_price or 0) - float(prev.unit_price or 0)) * qty
            out.append({
                "kind": "price_jump",
                "at": curr.created_at.isoformat(),
                "from_price": float(prev.unit_price or 0),
                "to_price": float(curr.unit_price or 0),
                "pct_change": round(pct, 2),
                "quantity": qty,
                "impact": round(max(0.0, impact), 2),
                "supplier_id": curr.supplier_id,
                "purchase_id": curr.purchase_id,
                "message_tj": f"Narx {prev.unit_price} -> {curr.unit_price} (+{pct:.1f}%)",
            })
    return out


def _detect_outliers(entries, cfg):
    if len(entries) < cfg.min_history:
        return []
    prices = [float(e.unit_price or 0) for e in entries]
    zs = _robust_z(prices)
    med = median(prices)
    out = []
    for e, z in zip(entries, zs):
        if abs(z) >= cfg.z_threshold and z > 0:
            qty = float(e.quantity or 0)
            impact = (float(e.unit_price or 0) - med) * qty
            out.append({
                "kind": "price_outlier",
                "at": e.created_at.isoformat(),
                "price": float(e.unit_price or 0),
                "median_price": round(med, 2),
                "z_score": round(z, 2),
                "quantity": qty,
                "impact": round(max(0.0, impact), 2),
                "supplier_id": e.supplier_id,
                "purchase_id": e.purchase_id,
                "message_tj": f"Narx {e.unit_price} medianadan {z:.1f}s yuqori",
            })
    return out


def _detect_trend(entries, cfg):
    if len(entries) < cfg.trend_min_points:
        return None
    first = float(entries[0].unit_price or 0)
    last = float(entries[-1].unit_price or 0)
    pct = _pct_change(first, last)
    if pct < cfg.trend_pct:
        return None
    last_qty = float(entries[-1].quantity or 0)
    impact = (last - first) * last_qty
    return {
        "kind": "upward_trend",
        "points": len(entries),
        "first_price": first,
        "last_price": last,
        "pct_change": round(pct, 2),
        "quantity": last_qty,
        "impact": round(max(0.0, impact), 2),
        "supplier_id": entries[-1].supplier_id,
        "message_tj": f"{len(entries)} ta xaridda narx {first} -> {last} (+{pct:.1f}%)",
    }


def _detect_supplier_gaps(entries, cfg):
    by_supplier = {}
    for e in entries:
        if e.supplier_id is None:
            continue
        by_supplier.setdefault(e.supplier_id, []).append(e)
    if len(by_supplier) < 2:
        return []
    medians = {sid: median([float(e.unit_price or 0) for e in rows])
               for sid, rows in by_supplier.items()}
    overall = median(list(medians.values()))
    if overall <= 0:
        return []
    out = []
    for sid, rows in by_supplier.items():
        med = medians[sid]
        gap = _pct_change(overall, med)
        if gap < cfg.supplier_gap_pct:
            continue
        total_qty = sum(float(e.quantity or 0) for e in rows)
        impact = (med - overall) * total_qty
        out.append({
            "kind": "supplier_gap",
            "supplier_id": sid,
            "median_price": round(med, 2),
            "market_median": round(overall, 2),
            "pct_vs_market": round(gap, 2),
            "quantity": round(total_qty, 2),
            "samples": len(rows),
            "impact": round(max(0.0, impact), 2),
            "message_tj": f"Boshqa supplierlarga nisbatan {gap:.1f}% qimmat",
        })
    out.sort(key=lambda x: x["impact"], reverse=True)
    return out


def _group_by_product(rows):
    out = {}
    for r in rows:
        out.setdefault(r.product_id, []).append(r)
    return out


def analyze(db, tenant_id, cfg=None, product_id=None):
    cfg = cfg or AnomalyConfig()
    now = datetime.utcnow()
    since = now - timedelta(days=cfg.window_days)
    pid_filter = [product_id] if product_id else None
    rows = _load_history(db, tenant_id, since, pid_filter)
    if not rows:
        return _empty(cfg, now, product_id)
    grouped = _group_by_product(rows)
    products = {p.id: p for p in db.query(Product).filter(
        Product.tenant_id == tenant_id,
        Product.id.in_(list(grouped.keys()))).all()}
    suppliers = {s.id: s for s in db.query(Supplier).filter(
        Supplier.tenant_id == tenant_id).all()}
    supplier_agg = {}

    def _touch_supplier(sid):
        if sid not in supplier_agg:
            supplier_agg[sid] = {
                "supplier_id": sid,
                "supplier_name": suppliers[sid].name if sid in suppliers else "?",
                "anomaly_count": 0,
                "products": set(),
                "estimated_impact": 0.0,
            }
        return supplier_agg[sid]

    product_findings = []
    total_impact = 0.0
    for pid, entries in grouped.items():
        if len(entries) < cfg.min_history:
            continue
        findings = []
        findings += _detect_jumps(entries, cfg)
        findings += _detect_outliers(entries, cfg)
        trend = _detect_trend(entries, cfg)
        if trend:
            findings.append(trend)
        findings += _detect_supplier_gaps(entries, cfg)
        if not findings:
            continue
        total_purchase_value = round(sum(float(e.total or 0) for e in entries), 2)
        estimated_impact = round(sum(f.get("impact", 0.0) for f in findings), 2)
        total_impact += estimated_impact
        p = products.get(pid)
        product_findings.append({
            "product_id": pid,
            "sku": p.sku if p else None,
            "name": p.name if p else "?",
            "history_points": len(entries),
            "total_purchase_value": total_purchase_value,
            "estimated_impact": estimated_impact,
            "findings": findings,
        })
        for f in findings:
            sid = f.get("supplier_id")
            if sid is None:
                continue
            b = _touch_supplier(sid)
            b["anomaly_count"] += 1
            b["products"].add(pid)
            b["estimated_impact"] += float(f.get("impact", 0.0) or 0.0)

    product_findings.sort(key=lambda x: x["estimated_impact"], reverse=True)
    suppliers_out = [{"supplier_id": b["supplier_id"],
                      "supplier_name": b["supplier_name"],
                      "anomaly_count": b["anomaly_count"],
                      "products_count": len(b["products"]),
                      "estimated_impact": round(b["estimated_impact"], 2)}
                     for b in supplier_agg.values()]
    suppliers_out.sort(key=lambda x: x["estimated_impact"], reverse=True)
    return {
        "config": cfg.to_dict(),
        "generated_at": now.isoformat(),
        "window_days": cfg.window_days,
        "product_id": product_id,
        "summary": {
            "products_analyzed": len(grouped),
            "products_with_anomalies": len(product_findings),
            "total_estimated_impact": round(total_impact, 2),
            "suppliers_flagged": len(suppliers_out),
        },
        "suppliers": suppliers_out,
        "products": product_findings,
    }


def by_supplier(db, tenant_id, supplier_id, cfg=None):
    full = analyze(db, tenant_id, cfg)
    out = []
    for p in full["products"]:
        hits = [f for f in p["findings"]
                if f.get("supplier_id") == supplier_id]
        if hits:
            out.append({**{k: v for k, v in p.items() if k != "findings"},
                        "findings": hits,
                        "estimated_impact": round(
                            sum(f.get("impact", 0.0) for f in hits), 2)})
    out.sort(key=lambda x: x["estimated_impact"], reverse=True)
    return out


def _empty(cfg, now, product_id):
    return {
        "config": cfg.to_dict(),
        "generated_at": now.isoformat(),
        "window_days": cfg.window_days,
        "product_id": product_id,
        "summary": {"products_analyzed": 0, "products_with_anomalies": 0,
                    "total_estimated_impact": 0.0, "suppliers_flagged": 0},
        "suppliers": [],
        "products": [],
    }
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
    print(f"\nRound 2c: {count} ta fayl")
    subprocess.run(["git", "add", "-A"], check=False)
    subprocess.run(["git", "commit", "-m", "Round 2c: dead_stock_tiers + supplier_anomaly"], check=False)
    subprocess.run(["git", "push", "origin", "main"], check=False)
    print("GitHub'ga yuborildi")


if __name__ == "__main__":
    main()
