from dataclasses import dataclass, asdict
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.product import Product, InventoryMovement
from app.models.inventory_count import InventoryCount
from app.models.company import Branch
from app.models.user import User
from app.intelligence.costing import moving_average_cost, CostingMethod


@dataclass
class VarianceConfig:
    min_value: float = 0.0
    significant_pct: float = 1.0
    critical_pct: float = 5.0
    critical_value: float = 1000.0
    recurring_min_events: int = 3
    use_costing_engine: bool = False
    costing_method: CostingMethod = CostingMethod.MOVING_AVERAGE

    def to_dict(self):
        return {**asdict(self), "costing_method": self.costing_method.value}


def _severity(pct, val, cfg):
    ap, av = abs(pct), abs(val)
    if av >= cfg.critical_value * 2:
        return "critical"
    if ap >= cfg.critical_pct or av >= cfg.critical_value:
        return "high"
    if ap >= cfg.significant_pct:
        return "medium"
    return "low"


def _explain(d, qty, pct, sev):
    if d == "OK":
        return "Farq yoq."
    if d == "LOSS":
        return f"{abs(qty):.0f} dona yoqotilgan ({abs(pct):.1f}%, {sev})."
    return f"{abs(qty):.0f} dona ortiqcha ({abs(pct):.1f}%, {sev})."


def _enrich(db, tenant_id, counts, cfg):
    if not counts:
        return []
    pids = list({c.product_id for c in counts})
    bids = list({c.branch_id for c in counts})
    uids = list({c.counted_by for c in counts if c.counted_by})
    products = {p.id: p for p in db.query(Product).filter(
        Product.tenant_id == tenant_id, Product.id.in_(pids)).all()}
    branches = {b.id: b for b in db.query(Branch).filter(
        Branch.tenant_id == tenant_id, Branch.id.in_(bids)).all()}
    users = {u.id: u for u in db.query(User).filter(
        User.tenant_id == tenant_id, User.id.in_(uids)).all()} if uids else {}
    out = []
    for c in counts:
        p = products.get(c.product_id)
        b = branches.get(c.branch_id)
        u = users.get(c.counted_by) if c.counted_by else None
        expected = float(c.expected_qty or 0)
        actual = float(c.actual_qty or 0)
        vqty = expected - actual
        ucost = float(c.unit_cost or 0)
        if cfg.use_costing_engine and p:
            try:
                ucost = moving_average_cost(db, tenant_id, c.product_id,
                                            as_of=c.created_at)
            except Exception:
                pass
        vval = vqty * ucost
        vpct = (vqty / expected * 100.0) if expected > 0 else 0.0
        sev = _severity(vpct, vval, cfg)
        if abs(vval) < cfg.min_value:
            continue
        d = "LOSS" if vqty > 0 else ("SURPLUS" if vqty < 0 else "OK")
        out.append({
            "count_id": c.id, "at": c.created_at.isoformat(),
            "branch_id": c.branch_id,
            "branch_name": b.name if b else None,
            "product_id": c.product_id,
            "sku": p.sku if p else None,
            "name": p.name if p else "?",
            "expected_qty": round(expected, 2),
            "actual_qty": round(actual, 2),
            "variance_qty": round(vqty, 2),
            "variance_pct": round(vpct, 2),
            "unit_cost": round(ucost, 2),
            "variance_value": round(vval, 2),
            "direction": d, "severity": sev,
            "counter_user_id": c.counted_by,
            "counter_name": u.full_name if u else None,
            "reason_tj": _explain(d, vqty, vpct, sev),
        })
    out.sort(key=lambda x: abs(x["variance_value"]), reverse=True)
    return out


_SEV = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _find_recurring(items, cfg):
    buckets = {}
    for it in items:
        if it["direction"] != "LOSS" or it["severity"] == "low":
            continue
        k = (it["product_id"], it["branch_id"])
        b = buckets.setdefault(k, {
            "product_id": it["product_id"], "sku": it["sku"],
            "name": it["name"], "branch_id": it["branch_id"],
            "branch_name": it["branch_name"], "events": 0,
            "total_lost_value": 0.0, "total_lost_qty": 0.0,
            "last_at": None})
        b["events"] += 1
        b["total_lost_value"] += abs(it["variance_value"])
        b["total_lost_qty"] += abs(it["variance_qty"])
        b["last_at"] = max(b["last_at"] or it["at"], it["at"])
    out = [b for b in buckets.values() if b["events"] >= cfg.recurring_min_events]
    for b in out:
        b["total_lost_value"] = round(b["total_lost_value"], 2)
        b["total_lost_qty"] = round(b["total_lost_qty"], 2)
    out.sort(key=lambda x: x["total_lost_value"], reverse=True)
    return out


def analyze(db, tenant_id, since, until, branch_id=None, cfg=None):
    cfg = cfg or VarianceConfig()
    now = datetime.utcnow()
    q = db.query(InventoryCount).filter(
        InventoryCount.tenant_id == tenant_id,
        InventoryCount.created_at >= since,
        InventoryCount.created_at <= until)
    if branch_id:
        q = q.filter(InventoryCount.branch_id == branch_id)
    counts = q.order_by(InventoryCount.created_at.desc()).all()
    items = _enrich(db, tenant_id, counts, cfg)
    lv = sum(abs(i["variance_value"]) for i in items if i["direction"] == "LOSS")
    sv = sum(abs(i["variance_value"]) for i in items if i["direction"] == "SURPLUS")
    lq = sum(abs(i["variance_qty"]) for i in items if i["direction"] == "LOSS")
    sq = sum(abs(i["variance_qty"]) for i in items if i["direction"] == "SURPLUS")
    sev_count = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    for i in items:
        sev_count[i["severity"]] = sev_count.get(i["severity"], 0) + 1
    recurring = _find_recurring(items, cfg)
    return {
        "config": cfg.to_dict(),
        "summary": {
            "total_counts": len(counts),
            "total_items_analyzed": len(items),
            "loss_qty": round(lq, 2), "loss_value": round(lv, 2),
            "surplus_qty": round(sq, 2), "surplus_value": round(sv, 2),
            "net_loss_value": round(lv - sv, 2),
            "recurring_offenders": len(recurring),
            "by_severity": sev_count},
        "recurring_offenders": recurring,
        "items": items,
        "generated_at": now.isoformat(),
    }
