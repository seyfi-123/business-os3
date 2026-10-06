from dataclasses import dataclass, asdict
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
