from dataclasses import dataclass, asdict
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
