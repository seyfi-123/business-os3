"""
Business OS — PART A
Intelligence rest + AI + main + Billing + Notifications
"""
import os
import subprocess

FILES = {}

FILES["backend/app/intelligence/variance.py"] = '''from dataclasses import dataclass, asdict
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
'''

FILES["backend/app/intelligence/forecast.py"] = '''from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from statistics import mean, pstdev
from collections import defaultdict
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.sale import Sale, SaleItem
from app.models.product import Product, Inventory


@dataclass
class ForecastConfig:
    history_days: int = 90
    recent_weight_days: int = 14
    recent_weight: float = 2.0
    min_history_days: int = 14
    stockout_days: int = 7
    excess_days: int = 60
    cash_forecast_days: int = 30
    top_products_limit: int = 50

    def to_dict(self):
        return asdict(self)


def _chunks(seq, size):
    seq = list(seq)
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _daily_revenue(db, tenant_id, since, until, branch_id=None):
    q = (db.query(func.date(Sale.created_at), func.sum(Sale.total))
         .filter(Sale.tenant_id == tenant_id,
                 Sale.created_at >= since,
                 Sale.created_at <= until))
    if branch_id:
        q = q.filter(Sale.branch_id == branch_id)
    return {d: float(v or 0) for d, v in
            q.group_by(func.date(Sale.created_at)).all()}


def _daily_product_qty(db, tenant_id, pids, since, until, branch_id=None):
    out = defaultdict(dict)
    for chunk in _chunks(pids, 500):
        q = (db.query(SaleItem.product_id, func.date(Sale.created_at),
                      func.sum(SaleItem.quantity))
             .join(Sale, Sale.id == SaleItem.sale_id)
             .filter(SaleItem.tenant_id == tenant_id,
                     SaleItem.product_id.in_(chunk),
                     Sale.created_at >= since,
                     Sale.created_at <= until))
        if branch_id:
            q = q.filter(Sale.branch_id == branch_id)
        for pid, d, qty in q.group_by(SaleItem.product_id,
                                       func.date(Sale.created_at)).all():
            out[pid][d] = float(qty or 0)
    return out


def _weighted_avg(daily, as_of, hd, rwd, rw):
    if not daily:
        return 0.0
    cutoff = as_of - timedelta(days=hd)
    rc = as_of - timedelta(days=rwd)
    tw = tv = 0.0
    for d, v in daily.items():
        if d < cutoff or d > as_of:
            continue
        w = rw if d >= rc else 1.0
        tw += w
        tv += v * w
    return (tv / tw) if tw > 0 else 0.0


def _slope(daily, as_of, hd):
    cutoff = as_of - timedelta(days=hd)
    pts = sorted((d, v) for d, v in daily.items() if cutoff <= d <= as_of)
    if len(pts) < 3:
        return 0.0
    xs = list(range(len(pts)))
    ys = [v for _, v in pts]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else 0.0


def _dow_factors(daily, as_of, hd):
    cutoff = as_of - timedelta(days=hd)
    by = defaultdict(list)
    for d, v in daily.items():
        if cutoff <= d <= as_of:
            by[d.weekday()].append(v)
    if not by:
        return {i: 1.0 for i in range(7)}
    overall = mean([v for vals in by.values() for v in vals]) or 1.0
    return {i: (mean(by.get(i, [])) / overall) if by.get(i) and overall > 0 else 1.0
            for i in range(7)}


def _conf(daily, as_of, hd):
    cutoff = as_of - timedelta(days=hd)
    vals = [v for d, v in daily.items() if cutoff <= d <= as_of]
    n = len(vals)
    if n < 5:
        return {"level": "low", "cv": None, "samples": n}
    m = mean(vals)
    if m <= 0:
        return {"level": "low", "cv": None, "samples": n}
    cv = pstdev(vals) / m
    if cv < 0.4 and n >= 30:
        lvl = "high"
    elif cv < 0.8 and n >= 14:
        lvl = "medium"
    else:
        lvl = "low"
    return {"level": lvl, "cv": round(cv, 3), "samples": n}


def _stock(db, tenant_id, branch_id, pid):
    inv = db.query(Inventory).filter(
        Inventory.tenant_id == tenant_id,
        Inventory.branch_id == branch_id,
        Inventory.product_id == pid).first()
    return float(inv.quantity if inv else 0)


def _fcast(daily, as_of, days, cfg, seasonal=True):
    avg = _weighted_avg(daily, as_of, cfg.history_days,
                        cfg.recent_weight_days, cfg.recent_weight)
    slope = _slope(daily, as_of, cfg.history_days)
    factors = _dow_factors(daily, as_of, cfg.history_days) if seasonal else {}
    out = []
    for i in range(1, days + 1):
        d = as_of + timedelta(days=i)
        base = max(0.0, avg + slope * i)
        f = factors.get(d.weekday(), 1.0) if seasonal else 1.0
        out.append({"date": d.isoformat(), "value": round(base * f, 2)})
    return out


def _sum(items):
    return round(sum(x["value"] for x in items), 2)


def revenue_forecast(db, tenant_id, branch_id=None, cfg=None):
    cfg = cfg or ForecastConfig()
    now = datetime.utcnow()
    today = now.date()
    since = now - timedelta(days=cfg.history_days)
    daily = _daily_revenue(db, tenant_id, since, now, branch_id)
    if len(daily) < cfg.min_history_days:
        return {"available": False,
                "reason": f"Tarix yetarli emas ({len(daily)}/{cfg.min_history_days})"}
    f7 = _fcast(daily, today, 7, cfg)
    f14 = _fcast(daily, today, 14, cfg)
    f30 = _fcast(daily, today, 30, cfg)
    recent = sum(v for d, v in daily.items() if (today - d).days <= 7)
    return {"available": True, "generated_at": now.isoformat(),
            "recent_7d_revenue": round(recent, 2),
            "forecast": {"7d": {"total": _sum(f7), "daily": f7},
                         "14d": {"total": _sum(f14), "daily": f14},
                         "30d": {"total": _sum(f30), "daily": f30}},
            "confidence": _conf(daily, today, cfg.history_days)}


def product_demand_forecast(db, tenant_id, pids, branch_id=None,
                            days_ahead=14, cfg=None):
    cfg = cfg or ForecastConfig()
    now = datetime.utcnow()
    today = now.date()
    since = now - timedelta(days=cfg.history_days)
    dm = _daily_product_qty(db, tenant_id, pids, since, now, branch_id)
    products = {p.id: p for p in db.query(Product).filter(
        Product.tenant_id == tenant_id, Product.id.in_(pids)).all()}
    out = []
    for pid in pids:
        daily = dm.get(pid, {})
        p = products.get(pid)
        if not p:
            continue
        avg = _weighted_avg(daily, today, cfg.history_days,
                            cfg.recent_weight_days, cfg.recent_weight)
        slope = _slope(daily, today, cfg.history_days)
        fc = _fcast(daily, today, days_ahead, cfg)
        out.append({"product_id": pid, "sku": p.sku, "name": p.name,
                    "velocity": round(avg, 3),
                    "trend_dir": "up" if slope > 0.01 else ("down" if slope < -0.01 else "flat"),
                    "forecast_qty": _sum(fc), "days_ahead": days_ahead,
                    "confidence": _conf(daily, today, cfg.history_days)})
    out.sort(key=lambda x: x["forecast_qty"], reverse=True)
    return out


def stockout_risk(db, tenant_id, branch_id, pids=None, cfg=None):
    cfg = cfg or ForecastConfig()
    if pids is None:
        rows = (db.query(Inventory.product_id).filter(
            Inventory.tenant_id == tenant_id,
            Inventory.branch_id == branch_id).distinct().all())
        pids = [r[0] for r in rows]
    if not pids:
        return []
    fc = product_demand_forecast(db, tenant_id, pids, branch_id,
                                 days_ahead=cfg.stockout_days, cfg=cfg)
    stock = {pid: _stock(db, tenant_id, branch_id, pid) for pid in pids}
    out = []
    for f in fc:
        pid = f["product_id"]
        avail = stock.get(pid, 0)
        need = f["forecast_qty"]
        if f["velocity"] <= 0:
            dts, risk = None, "none"
        elif avail <= 0:
            dts, risk = 0, "critical"
        else:
            dts = round(avail / f["velocity"], 1)
            risk = "critical" if dts <= 3 else ("high" if dts <= 7 else ("medium" if dts <= 14 else "low"))
        out.append({"product_id": pid, "sku": f["sku"], "name": f["name"],
                    "current_stock": round(avail, 2), "velocity": f["velocity"],
                    "days_until_stockout": dts,
                    "forecast_qty_next_period": need,
                    "gap": round(avail - need, 2), "risk": risk,
                    "confidence": f["confidence"]})
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "none": 4}
    out.sort(key=lambda x: (rank.get(x["risk"], 5), -(x["days_until_stockout"] or 999)))
    return out


def excess_stock_risk(db, tenant_id, branch_id, pids=None, cfg=None):
    cfg = cfg or ForecastConfig()
    if pids is None:
        rows = (db.query(Inventory.product_id).filter(
            Inventory.tenant_id == tenant_id,
            Inventory.branch_id == branch_id).distinct().all())
        pids = [r[0] for r in rows]
    if not pids:
        return []
    fc = product_demand_forecast(db, tenant_id, pids, branch_id,
                                 days_ahead=30, cfg=cfg)
    stock = {pid: _stock(db, tenant_id, branch_id, pid) for pid in pids}
    products = {p.id: p for p in db.query(Product).filter(
        Product.tenant_id == tenant_id, Product.id.in_(pids)).all()}
    out = []
    for f in fc:
        pid = f["product_id"]
        v = f["velocity"]
        avail = stock.get(pid, 0)
        if avail <= 0:
            continue
        if v <= 0:
            dc, risk = None, "critical"
        else:
            dc = round(avail / v, 1)
            risk = "critical" if dc >= cfg.excess_days * 2 else ("high" if dc >= cfg.excess_days else ("medium" if dc >= cfg.excess_days * 0.7 else "low"))
        if risk == "low":
            continue
        p = products.get(pid)
        uc = float(p.cost_price or 0) if p else 0
        out.append({"product_id": pid, "sku": f["sku"], "name": f["name"],
                    "current_stock": round(avail, 2), "velocity": v,
                    "days_of_cover": dc, "capital_locked": round(avail * uc, 2),
                    "risk": risk, "confidence": f["confidence"]})
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    out.sort(key=lambda x: (rank.get(x["risk"], 5), -x["capital_locked"]))
    return out
'''

FILES["backend/app/intelligence/optimization.py"] = '''from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from enum import Enum
from sqlalchemy.orm import Session

from app.models.product import Product
from app.models.company import Branch
from app.intelligence.dead_stock_tiers import classify as dst_classify, TiersConfig
from app.intelligence.supplier_anomaly import analyze as sa_analyze, AnomalyConfig
from app.intelligence.variance import analyze as var_analyze, VarianceConfig
from app.intelligence.forecast import stockout_risk as fc_stockout, excess_stock_risk as fc_excess, ForecastConfig


class Action(str, Enum):
    BUY = "BUY"; REORDER = "REORDER"; TRANSFER = "TRANSFER"
    DISCOUNT = "DISCOUNT"; PROMOTE = "PROMOTE"; FIX = "FIX"
    NEGOTIATE = "NEGOTIATE"; MONITOR = "MONITOR"


class Priority(str, Enum):
    CRITICAL = "critical"; HIGH = "high"; MEDIUM = "medium"; LOW = "low"


@dataclass
class OptimizationConfig:
    period_days: int = 90
    variance_window_days: int = 90
    supplier_window_days: int = 180
    forecast_history_days: int = 90
    urgency_critical: float = 1.5
    urgency_high: float = 1.2
    urgency_medium: float = 1.0
    urgency_low: float = 0.7
    min_impact_tjs: float = 100.0
    top_actions_limit: int = 50
    critical_threshold: float = 5000.0
    high_threshold: float = 2000.0
    medium_threshold: float = 500.0
    def to_dict(self):
        return asdict(self)


def _score(imp, urg, conf):
    cw = {"high": 1.0, "medium": 0.85, "low": 0.65}.get(conf, 0.7)
    return abs(imp) * urg * cw


def _prio(score, cfg):
    if score >= cfg.critical_threshold:
        return Priority.CRITICAL
    if score >= cfg.high_threshold:
        return Priority.HIGH
    if score >= cfg.medium_threshold:
        return Priority.MEDIUM
    return Priority.LOW


def _urg(risk, cfg):
    return {"critical": cfg.urgency_critical, "high": cfg.urgency_high,
            "medium": cfg.urgency_medium, "low": cfg.urgency_low,
            "none": 0.5}.get(risk, cfg.urgency_medium)


def _lookup_product(db, tenant_id, pid):
    return db.query(Product).filter(Product.id == pid,
                                     Product.tenant_id == tenant_id).first()


def _stockout_signals(db, tid, bid, cfg):
    items = fc_stockout(db, tid, bid, None, ForecastConfig(history_days=cfg.forecast_history_days))
    out = []
    for it in items:
        if it["risk"] not in ("critical", "high"):
            continue
        p = _lookup_product(db, tid, it["product_id"])
        uc = float(p.cost_price or 0) if p else 0
        imp = it["forecast_qty_next_period"] * uc * 0.3
        out.append({"source": "forecast.stockout", "product_id": it["product_id"],
                    "branch_id": bid, "impact": round(imp, 2),
                    "urgency": _urg(it["risk"], cfg),
                    "confidence": it["confidence"]["level"],
                    "risk": it["risk"], "details": it})
    return out


def _excess_signals(db, tid, bid, cfg):
    items = fc_excess(db, tid, bid, None, ForecastConfig(history_days=cfg.forecast_history_days))
    out = []
    for it in items:
        if it["risk"] not in ("critical", "high"):
            continue
        out.append({"source": "forecast.excess", "product_id": it["product_id"],
                    "branch_id": bid, "impact": it["capital_locked"],
                    "urgency": _urg(it["risk"], cfg),
                    "confidence": it["confidence"]["level"],
                    "risk": it["risk"], "details": it})
    return out


def _dead_signals(db, tid, cfg):
    r = dst_classify(db, tid, branch_id=None, cfg=TiersConfig(), include_normal=False)
    out = []
    for it in r["items"]:
        t = it["tier"]
        if t == "NORMAL":
            continue
        out.append({"source": f"dead_stock.{t.lower()}",
                    "product_id": it["product_id"], "branch_id": None,
                    "impact": it["capital_locked"],
                    "urgency": {"DEAD": cfg.urgency_high, "SLOW": cfg.urgency_medium,
                                "AT-RISK": cfg.urgency_medium}.get(t, cfg.urgency_low),
                    "confidence": "medium", "details": it})
    return out


def _supplier_signals(db, tid, cfg):
    r = sa_analyze(db, tid, AnomalyConfig(window_days=cfg.supplier_window_days))
    out = []
    for p in r["products"]:
        for f in p["findings"]:
            if f["kind"] not in ("price_jump", "upward_trend", "supplier_gap"):
                continue
            imp = float(f.get("impact", 0) or 0)
            if imp <= 0:
                continue
            out.append({"source": f"supplier_anomaly.{f['kind']}",
                        "product_id": p["product_id"], "branch_id": None,
                        "supplier_id": f.get("supplier_id"), "impact": imp,
                        "urgency": cfg.urgency_high if f["kind"] == "upward_trend" else cfg.urgency_medium,
                        "confidence": "medium",
                        "details": {"product_name": p["name"], "finding": f}})
    return out


def _var_signals(db, tid, cfg):
    until = datetime.utcnow()
    since = until - timedelta(days=cfg.variance_window_days)
    r = var_analyze(db, tid, since, until, branch_id=None, cfg=VarianceConfig())
    out = []
    for it in r["items"]:
        if it["direction"] != "LOSS" or it["severity"] not in ("high", "critical"):
            continue
        out.append({"source": f"variance.{it['severity']}",
                    "product_id": it["product_id"], "branch_id": it["branch_id"],
                    "branch_name": it["branch_name"],
                    "impact": abs(it["variance_value"]),
                    "urgency": cfg.urgency_critical if it["severity"] == "critical" else cfg.urgency_high,
                    "confidence": "high", "details": it})
    return out


def _enrich(db, tid, signals):
    bids = {s["branch_id"] for s in signals if s.get("branch_id")}
    if bids:
        bs = {b.id: b.name for b in db.query(Branch).filter(
            Branch.tenant_id == tid, Branch.id.in_(bids)).all()}
        for s in signals:
            if s.get("branch_id") and not s.get("branch_name"):
                s["branch_name"] = bs.get(s["branch_id"])
    pids = {s["product_id"] for s in signals}
    if pids:
        ps = {p.id: p for p in db.query(Product).filter(
            Product.tenant_id == tid, Product.id.in_(pids)).all()}
        for s in signals:
            p = ps.get(s["product_id"])
            s["sku"] = p.sku if p else None
            s["name"] = p.name if p else "?"


def _to_action(sig, cfg):
    src = sig["source"]
    if src == "forecast.stockout":
        return Action.REORDER if sig["urgency"] >= cfg.urgency_critical else Action.BUY
    if src == "forecast.excess": return Action.TRANSFER
    if src == "dead_stock.dead": return Action.DISCOUNT
    if src == "dead_stock.slow": return Action.PROMOTE
    if src == "dead_stock.at-risk": return Action.MONITOR
    if src in ("supplier_anomaly.upward_trend", "supplier_anomaly.price_jump"):
        return Action.NEGOTIATE
    if src == "supplier_anomaly.supplier_gap": return Action.FIX
    if src.startswith("variance."): return Action.FIX
    return None


def generate(db, tenant_id, branch_id=None, cfg=None):
    cfg = cfg or OptimizationConfig()
    now = datetime.utcnow()
    signals = []
    if branch_id is not None:
        signals += _stockout_signals(db, tenant_id, branch_id, cfg)
        signals += _excess_signals(db, tenant_id, branch_id, cfg)
    signals += _dead_signals(db, tenant_id, cfg)
    signals += _supplier_signals(db, tenant_id, cfg)
    signals += _var_signals(db, tenant_id, cfg)
    _enrich(db, tenant_id, signals)
    signals = [s for s in signals if s["impact"] >= cfg.min_impact_tjs]

    actions = []
    for s in signals:
        a = _to_action(s, cfg)
        if a is None:
            continue
        sc = _score(s["impact"], s["urgency"], s["confidence"])
        actions.append({"action": a, "source": s["source"],
                        "product_id": s["product_id"], "sku": s.get("sku"),
                        "name": s.get("name"), "branch_id": s.get("branch_id"),
                        "branch_name": s.get("branch_name"),
                        "supplier_id": s.get("supplier_id"),
                        "impact": s["impact"], "urgency": s["urgency"],
                        "confidence": s["confidence"], "score": round(sc, 2),
                        "priority": _prio(sc, cfg).value,
                        "evidence": s.get("details")})

    seen = {}
    for a in actions:
        k = (a["action"].value, a["product_id"], a.get("branch_id") or 0, a.get("supplier_id") or 0)
        if k not in seen or a["score"] > seen[k]["score"]:
            seen[k] = a
    actions = list(seen.values())
    actions.sort(key=lambda x: x["score"], reverse=True)
    actions = actions[:cfg.top_actions_limit]

    bp = {p.value: 0 for p in Priority}
    ba = {a.value: 0 for a in Action}
    ti = 0.0
    for a in actions:
        bp[a["priority"]] += 1
        ba[a["action"].value] += 1
        ti += a["impact"]

    return {"generated_at": now.isoformat(),
            "summary": {"total_actions": len(actions),
                        "total_potential_impact": round(ti, 2),
                        "by_priority": bp, "by_action": ba},
            "actions": [{**a, "action": a["action"].value} for a in actions]}
'''

FILES["backend/app/ai/__init__.py"] = '''from app.ai.context_builder import build_context
from app.ai.copilot import ask, CopilotResponse

__all__ = ["build_context", "ask", "CopilotResponse"]
'''

FILES["backend/app/ai/context_builder.py"] = '''from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models.tenant import Tenant
from app.models.company import Branch


@dataclass
class ContextConfig:
    period_days: int = 30
    variance_days: int = 90
    supplier_days: int = 180
    forecast_history_days: int = 90
    top_narrative_actions: int = 5
    top_locked_limit: int = 10
    supplier_top: int = 5
    def to_dict(self):
        return asdict(self)


def _safe(fn, default=None, section="module", **kw):
    try:
        return fn(**kw)
    except Exception:
        return default if default is not None else {"available": False}


def _fmt(v, cur="TJS"):
    try:
        return f"{float(v):,.0f} {cur}"
    except Exception:
        return str(v)


def build_context(db, tenant_id, branch_id=None, cfg=None):
    cfg = cfg or ContextConfig()
    now = datetime.utcnow()
    since = now - timedelta(days=cfg.period_days)
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if branch_id is not None:
        own = db.query(Branch).filter(Branch.id == branch_id,
                                       Branch.tenant_id == tenant_id).first()
        if not own:
            from fastapi import HTTPException
            raise HTTPException(404, f"Filial topilmadi: {branch_id}")

    from app.intelligence.profit import real_profit
    from app.intelligence.leakage import detect_leakage
    from app.intelligence.dead_stock_tiers import summary as dst_summary, top_locked, TiersConfig
    from app.intelligence.supplier_anomaly import analyze as sa_analyze, AnomalyConfig
    from app.intelligence.variance import analyze as var_analyze, VarianceConfig
    from app.intelligence.forecast import revenue_forecast, stockout_risk, excess_stock_risk, ForecastConfig
    from app.intelligence.optimization import generate as opt_generate, OptimizationConfig

    profit = _safe(real_profit, db=db, tenant_id=tenant_id, since=since,
                   until=now, branch_id=branch_id)
    leakage = _safe(detect_leakage, db=db, tenant_id=tenant_id)
    dst_cfg = TiersConfig()
    dst_sum = _safe(dst_summary, db=db, tenant_id=tenant_id,
                    branch_id=branch_id, cfg=dst_cfg)
    dst_top = _safe(top_locked, default=[], db=db, tenant_id=tenant_id,
                    limit=cfg.top_locked_limit, branch_id=branch_id, cfg=dst_cfg)
    sa = _safe(sa_analyze, db=db, tenant_id=tenant_id,
               cfg=AnomalyConfig(window_days=cfg.supplier_days))
    sa_top = (sa.get("suppliers") or [])[:cfg.supplier_top] if isinstance(sa, dict) else []
    var_since = now - timedelta(days=cfg.variance_days)
    variance = _safe(var_analyze, db=db, tenant_id=tenant_id, since=var_since,
                     until=now, branch_id=branch_id, cfg=VarianceConfig())
    fc_cfg = ForecastConfig(history_days=cfg.forecast_history_days)
    rev_fc = _safe(revenue_forecast, db=db, tenant_id=tenant_id,
                   branch_id=branch_id, cfg=fc_cfg)
    stockout, excess = [], []
    if branch_id is not None:
        stockout = _safe(stockout_risk, default=[], db=db, tenant_id=tenant_id,
                         branch_id=branch_id, cfg=fc_cfg)
        excess = _safe(excess_stock_risk, default=[], db=db, tenant_id=tenant_id,
                       branch_id=branch_id, cfg=fc_cfg)
    opt_cfg = OptimizationConfig(period_days=cfg.period_days,
                                  variance_window_days=cfg.variance_days,
                                  supplier_window_days=cfg.supplier_days,
                                  forecast_history_days=cfg.forecast_history_days)
    opt = _safe(opt_generate, db=db, tenant_id=tenant_id,
                branch_id=branch_id, cfg=opt_cfg)
    nar = _narrative(tenant, profit, leakage, dst_sum, sa_top, variance,
                     rev_fc, stockout, excess, opt, cfg.top_narrative_actions)
    return {"meta": {"generated_at": now.isoformat(), "period_days": cfg.period_days,
                     "branch_id": branch_id, "config": cfg.to_dict()},
            "company": {"tenant_id": tenant_id, "name": tenant.name if tenant else None,
                        "industry": tenant.industry if tenant else None,
                        "currency": tenant.currency if tenant else "TJS"},
            "period": {"since": since.isoformat(), "until": now.isoformat()},
            "profit": profit, "leakage": leakage,
            "dead_stock": {"summary": dst_sum, "top_locked": dst_top},
            "supplier_anomaly": {"summary": sa.get("summary") if isinstance(sa, dict) else None,
                                 "top_suppliers": sa_top},
            "variance": {"summary": variance.get("summary") if isinstance(variance, dict) else None,
                         "top_losses": (variance.get("items") or [])[:10] if isinstance(variance, dict) else [],
                         "recurring": (variance.get("recurring_offenders") or [])[:10] if isinstance(variance, dict) else []},
            "forecast": {"revenue": rev_fc,
                         "stockout_risk": stockout[:10] if stockout else [],
                         "excess_risk": excess[:10] if excess else []},
            "optimization": {"summary": opt.get("summary") if isinstance(opt, dict) else None,
                             "actions": (opt.get("actions") or [])[:20] if isinstance(opt, dict) else []},
            "narrative_tj": nar}


def _narrative(tenant, profit, leakage, dst, supplier, variance, revenue,
               stockout, excess, opt, top_n):
    cur = (tenant.currency if tenant else "TJS") or "TJS"
    out = {}
    if isinstance(profit, dict) and "revenue" in profit:
        out["profit_tj"] = f"Tushum {_fmt(profit['revenue'], cur)}, yalpi foyda {_fmt(profit['gross_profit'], cur)} ({profit.get('gross_margin_pct', 0)}%), sof foyda {_fmt(profit['net_profit'], cur)}."
    if isinstance(leakage, dict) and leakage.get("total_leak") is not None:
        out["leakage_tj"] = f"Potensial oqib ketish: {_fmt(leakage['total_leak'], cur)}."
    if isinstance(dst, dict) and dst.get("by_tier"):
        out["dead_stock_tj"] = f"Qotib qolgan kapital: {_fmt(dst.get('total_capital_locked', 0), cur)}."
    if supplier:
        out["supplier_tj"] = f"{len(supplier)} supplierda anomaliya."
    if isinstance(variance, dict) and variance.get("summary"):
        s = variance["summary"]
        out["variance_tj"] = f"Inventarizatsiyada yoqotish: {_fmt(s.get('loss_value', 0), cur)}."
    if isinstance(revenue, dict) and revenue.get("available"):
        out["forecast_tj"] = f"7 kun prognozi: {_fmt(revenue['forecast']['7d']['total'], cur)}."
    if stockout:
        crit = [s for s in stockout if s.get("risk") == "critical"]
        if crit:
            out["risks_tj"] = f"{len(crit)} mahsulot 3 kun ichida tugaydi."
    if isinstance(opt, dict) and opt.get("summary"):
        s = opt["summary"]
        out["headline_tj"] = f"{s.get('total_actions', 0)} harakat tavsiya etiladi. Potensial tasir: {_fmt(s.get('total_potential_impact', 0), cur)}."
        top = (opt.get("actions") or [])[:top_n]
        out["top_actions_tj"] = "\\n".join([f"{i+1}. [{a['priority'].upper()}] {a['action']} - {a.get('name')} - {_fmt(a['impact'], cur)}" for i, a in enumerate(top)])
    return out
'''

FILES["backend/app/ai/copilot.py"] = '''import os, json, logging
from dataclasses import dataclass
from typing import Literal
from sqlalchemy.orm import Session
from app.ai.context_builder import build_context, ContextConfig

logger = logging.getLogger(__name__)


@dataclass
class CopilotResponse:
    answer_tj: str
    mode: Literal["local", "openai"]
    intent: str
    used_sections: list
    def to_dict(self):
        return {"answer_tj": self.answer_tj, "mode": self.mode,
                "intent": self.intent, "used_sections": self.used_sections}


INTENTS = {
    "profit": ["foyda", "profit", "mаржа", "margin", "tushum", "revenue"],
    "leakage": ["oqib", "leak", "yoqotish", "zarar", "loss"],
    "dead": ["dead", "qotib", "zaxira", "stock", "eski"],
    "supplier": ["supplier", "taminotchi", "narx", "price", "qimmat"],
    "variance": ["variance", "inventarizatsiya", "sanoq", "count", "farq"],
    "forecast": ["prognoz", "forecast", "kelajak", "keyingi"],
    "action": ["nima qilish", "action", "tavsiya", "recommend", "harakat"],
}


def _intent(q):
    ql = q.lower()
    scores = {k: sum(1 for key in keys if key in ql) for k, keys in INTENTS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "overview"


def _local(intent, ctx):
    cur = ctx.get("company", {}).get("currency", "TJS")
    nar = ctx.get("narrative_tj", {})
    used = []
    def _f(v):
        try:
            return f"{float(v):,.0f} {cur}"
        except Exception:
            return str(v)
    if intent == "profit":
        p = ctx.get("profit") or {}
        used.append("profit")
        ans = f"Tushum: {_f(p.get('revenue', 0))}. Yalpi foyda: {_f(p.get('gross_profit', 0))} (mаржа {p.get('gross_margin_pct', 0)}%). Sof foyda: {_f(p.get('net_profit', 0))}."
        if nar.get("leakage_tj"):
            ans += f" {nar['leakage_tj']}"
        return CopilotResponse(ans, "local", intent, used)
    if intent == "leakage":
        l = ctx.get("leakage") or {}
        used.append("leakage")
        ans = f"Potensial oqib ketish: {_f(l.get('total_leak', 0))}."
        for f in (l.get("findings") or [])[:4]:
            ans += f" {f['message_tj']}"
        return CopilotResponse(ans, "local", intent, used)
    if intent == "dead":
        d = ctx.get("dead_stock") or {}
        s = d.get("summary") or {}
        used.append("dead_stock")
        return CopilotResponse(f"Qotib qolgan kapital: {_f(s.get('total_capital_locked', 0))}.", "local", intent, used)
    if intent == "supplier":
        sa = ctx.get("supplier_anomaly") or {}
        used.append("supplier_anomaly")
        tops = sa.get("top_suppliers") or []
        if not tops:
            return CopilotResponse("Supplier anomaliyalari topilmadi.", "local", intent, used)
        return CopilotResponse(f"{len(tops)} supplierda anomaliya.", "local", intent, used)
    if intent == "variance":
        v = ctx.get("variance") or {}
        s = v.get("summary") or {}
        used.append("variance")
        return CopilotResponse(f"Inventarizatsiyada yoqotish: {_f(s.get('loss_value', 0))}.", "local", intent, used)
    if intent == "forecast":
        r = (ctx.get("forecast") or {}).get("revenue") or {}
        used.append("forecast")
        if not r.get("available"):
            return CopilotResponse("Prognoz uchun tarix yetarli emas.", "local", intent, used)
        return CopilotResponse(f"7 kun: {_f(r['forecast']['7d']['total'])}. 30 kun: {_f(r['forecast']['30d']['total'])}.", "local", intent, used)
    if intent == "action":
        o = ctx.get("optimization") or {}
        acts = o.get("actions") or []
        used.append("optimization")
        if not acts:
            return CopilotResponse("Harakat talab qilinmaydi.", "local", intent, used)
        ans = "Tavsiya etilgan harakatlar:"
        for i, a in enumerate(acts[:5], 1):
            ans += f" {i}. [{a['priority'].upper()}] {a['action']} - {a.get('name')} ({_f(a['impact'])});"
        return CopilotResponse(ans, "local", intent, used)
    used.append("all")
    ans = nar.get("headline_tj") or ""
    for k in ("profit_tj", "leakage_tj", "dead_stock_tj", "supplier_tj", "variance_tj", "forecast_tj", "risks_tj"):
        if nar.get(k):
            ans += f" {nar[k]}"
    if nar.get("top_actions_tj"):
        ans += f"\\n\\n{nar['top_actions_tj']}"
    return CopilotResponse(ans.strip(), "local", "overview", used)


_SYS = "Sen Business OS AI Copilot'san. Faqat berilgan kontekst raqamlarini izohla. Raqamlarni qayta hisoblama. Tojiк tilida qisqa javob ber."


def _openai(question, ctx, intent):
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    slim = {"narrative_tj": ctx.get("narrative_tj"), "profit": ctx.get("profit"),
            "leakage": ctx.get("leakage"),
            "dead_stock_summary": (ctx.get("dead_stock") or {}).get("summary"),
            "supplier_summary": (ctx.get("supplier_anomaly") or {}).get("summary"),
            "variance_summary": (ctx.get("variance") or {}).get("summary"),
            "optimization_summary": (ctx.get("optimization") or {}).get("summary"),
            "top_actions": (ctx.get("optimization") or {}).get("actions", [])[:10]}
    try:
        client = OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "system", "content": _SYS},
                      {"role": "system", "content": "KONTEKST: " + json.dumps(slim, ensure_ascii=False, default=str)},
                      {"role": "user", "content": question}],
            temperature=0.2, max_tokens=800)
        return CopilotResponse(resp.choices[0].message.content.strip(),
                               "openai", intent, ["all"])
    except Exception:
        logger.exception("OpenAI failed")
        return None


def ask(db: Session, tenant_id: int, question: str, branch_id=None,
        use_openai=True, cfg=None):
    if not question or not question.strip():
        return CopilotResponse("Savolni yozing.", "local", "empty", [])
    ctx = build_context(db, tenant_id, branch_id, cfg)
    intent = _intent(question)
    if use_openai:
        oa = _openai(question, ctx, intent)
        if oa is not None:
            return oa
    return _local(intent, ctx)
'''

FILES["backend/app/main.py"] = '''from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api import auth, tenants, intelligence, ai
from app.api import sales, inventory, products, crm, suppliers, finance, audit

app = FastAPI(title=settings.APP_NAME, description=settings.APP_TAGLINE_TJ)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


@app.get("/")
def root():
    return {"app": settings.APP_NAME,
            "tagline_tj": settings.APP_TAGLINE_TJ, "status": "ok"}


for r in (auth, tenants, products, sales, inventory, crm, suppliers,
          finance, intelligence, ai, audit):
    app.include_router(r.router)
'''

FILES["backend/app/billing/__init__.py"] = "from app.billing import models  # noqa\\n"

FILES["backend/app/billing/config.py"] = '''import os


class BillingSettings:
    PAYMENT_MODE: str = os.getenv("BILLING_PAYMENT_MODE", "DEMO").upper()
    PROVIDER_WEBHOOK_SECRET: str = os.getenv("BILLING_WEBHOOK_SECRET", "")
    @property
    def demo_enabled(self):
        return self.PAYMENT_MODE == "DEMO"


billing_settings = BillingSettings()
'''

FILES["backend/app/billing/plans.py"] = '''from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Plan:
    code: str
    name_tj: str
    description_tj: str
    price_tjs: float
    billing_period_days: int
    trial_days: int
    limits: dict
    features: dict
    def to_dict(self):
        return asdict(self)


PLANS = {
    "STARTER": Plan("STARTER", "Стартер", "Yangi boshlagan biznes uchun", 0, 30, 14,
                    {"branches": 1, "employees": 3, "products": 200,
                     "invoices_per_month": 500, "purchases_per_month": 100},
                    {"intelligence": False, "ai_copilot": False, "api_access": False,
                     "multi_branch": False, "supplier_anomaly": False,
                     "variance": False, "forecast": False, "optimization": False}),
    "BUSINESS": Plan("BUSINESS", "Бизнес", "Osib borayotgan biznes uchun", 199, 30, 14,
                     {"branches": 5, "employees": 25, "products": 5000,
                      "invoices_per_month": 10000, "purchases_per_month": 2000},
                     {"intelligence": True, "ai_copilot": False, "api_access": False,
                      "multi_branch": True, "supplier_anomaly": True,
                      "variance": True, "forecast": True, "optimization": False}),
    "ENTERPRISE": Plan("ENTERPRISE", "Корхона", "Yirik korxonalar uchun", 499, 30, 14,
                       {"branches": None, "employees": None, "products": None,
                        "invoices_per_month": None, "purchases_per_month": None},
                       {"intelligence": True, "ai_copilot": True, "api_access": True,
                        "multi_branch": True, "supplier_anomaly": True,
                        "variance": True, "forecast": True, "optimization": True}),
}
DEFAULT_PLAN = "STARTER"


def get_plan(code):
    if code not in PLANS:
        raise ValueError(f"Nomahlum plan: {code}")
    return PLANS[code]


def list_plans():
    return [p.to_dict() for p in PLANS.values()]
'''

FILES["backend/app/billing/models.py"] = '''from sqlalchemy import (Column, Integer, String, Float, ForeignKey,
                        DateTime, Boolean, func, Index, text)
from app.core.database import Base


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (Index("ix_subscriptions_tenant_status", "tenant_id", "status"),)
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    plan_code = Column(String, nullable=False)
    status = Column(String, nullable=False, default="TRIAL")
    started_at = Column(DateTime, server_default=func.now())
    trial_end = Column(DateTime, nullable=True)
    current_period_start = Column(DateTime, nullable=False)
    current_period_end = Column(DateTime, nullable=False)
    cancelled_at = Column(DateTime, nullable=True)
    auto_renew = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Invoice(Base):
    __tablename__ = "billing_invoices"
    __table_args__ = (Index("ix_invoices_tenant_status", "tenant_id", "status"),)
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id"), nullable=True)
    number = Column(String, nullable=False, unique=True)
    plan_code = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String, default="TJS")
    status = Column(String, nullable=False, default="DRAFT")
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    issued_at = Column(DateTime, server_default=func.now())
    due_at = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    payment_method = Column(String, nullable=True)
    note = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
'''

FILES["backend/app/billing/schemas.py"] = '''from pydantic import BaseModel, Field
from datetime import datetime


class PlanOut(BaseModel):
    code: str; name_tj: str; description_tj: str
    price_tjs: float; billing_period_days: int; trial_days: int
    limits: dict; features: dict


class SubscriptionOut(BaseModel):
    id: int; tenant_id: int; plan_code: str
    plan: dict | None = None
    status: str
    started_at: datetime | None = None
    trial_end: datetime | None = None
    current_period_start: datetime
    current_period_end: datetime
    cancelled_at: datetime | None = None
    auto_renew: bool
    days_remaining: int | None = None


class SubscribeIn(BaseModel):
    plan_code: str = Field(min_length=2, max_length=32)


class InvoiceOut(BaseModel):
    id: int; number: str; tenant_id: int; plan_code: str
    amount: float; currency: str; status: str
    period_start: datetime; period_end: datetime
    issued_at: datetime | None = None
    due_at: datetime | None = None
    paid_at: datetime | None = None
    payment_method: str | None = None


class UsageItem(BaseModel):
    name: str; current: int | float
    limit: int | float | None
    used_pct: float | None = None


class UsageOut(BaseModel):
    plan_code: str | None = None
    status: str | None = None
    items: list[UsageItem]
'''

FILES["backend/app/billing/service.py"] = '''from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from fastapi import HTTPException

from app.models.company import Branch
from app.models.user import User
from app.models.product import Product
from app.models.sale import Sale
from app.models.supplier import Purchase
from app.models.audit import AuditLog
from app.billing.models import Subscription, Invoice
from app.billing.plans import PLANS, DEFAULT_PLAN, get_plan
from app.billing.config import billing_settings
from app.core.tx import transaction


def get_subscription(db, tenant_id):
    return (db.query(Subscription).filter(Subscription.tenant_id == tenant_id)
            .order_by(Subscription.id.desc()).first())


def get_active_subscription(db, tenant_id):
    now = datetime.utcnow()
    s = (db.query(Subscription).filter(
        Subscription.tenant_id == tenant_id,
        Subscription.status.in_(["TRIAL", "ACTIVE", "PAST_DUE"]))
         .order_by(Subscription.id.desc()).first())
    if not s:
        return None
    if s.status == "TRIAL" and s.trial_end and s.trial_end <= now:
        return None
    return s


def has_ever_had_trial(db, tenant_id):
    return (db.query(func.count(Subscription.id)).filter(
        Subscription.tenant_id == tenant_id,
        Subscription.trial_end.isnot(None)).scalar() or 0) > 0


def _insert_trial(db, tid, plan_code):
    plan = get_plan(plan_code)
    now = datetime.utcnow()
    sub = Subscription(tenant_id=tid, plan_code=plan.code, status="TRIAL",
                       started_at=now,
                       trial_end=now + timedelta(days=plan.trial_days),
                       current_period_start=now,
                       current_period_end=now + timedelta(days=plan.billing_period_days),
                       auto_renew=True)
    db.add(sub); db.flush(); db.refresh(sub)
    return sub


def get_or_create_subscription(db, tenant_id, plan_code=None):
    existing = get_active_subscription(db, tenant_id)
    if existing:
        return existing
    with transaction(db):
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": tenant_id})
        existing = get_active_subscription(db, tenant_id)
        if existing:
            return existing
        latest = (db.query(Subscription).filter(Subscription.tenant_id == tenant_id)
                  .order_by(Subscription.id.desc()).first())
        if latest is None:
            return _insert_trial(db, tenant_id, plan_code or DEFAULT_PLAN)
        return latest


def create_trial_subscription(db, tenant_id, plan_code=DEFAULT_PLAN):
    if has_ever_had_trial(db, tenant_id):
        raise HTTPException(400, "Bu tenant allaqachon TRIAL olgan")
    with transaction(db):
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": tenant_id})
        if has_ever_had_trial(db, tenant_id):
            raise HTTPException(400, "TRIAL allaqachon olingan")
        return _insert_trial(db, tenant_id, plan_code)


def _next_inv_num(db, tid):
    seq = db.execute(text("SELECT nextval('invoice_number_seq')")).scalar()
    return f"INV-{datetime.utcnow().year}-{tid:04d}-{int(seq):05d}"


def _issue_inv(db, sub, plan, now):
    inv = Invoice(tenant_id=sub.tenant_id, subscription_id=sub.id,
                  number=_next_inv_num(db, sub.tenant_id),
                  plan_code=plan.code, amount=plan.price_tjs, currency="TJS",
                  status="SENT", period_start=now,
                  period_end=now + timedelta(days=plan.billing_period_days),
                  due_at=now + timedelta(days=7))
    db.add(inv); db.flush()
    return inv


def change_plan(db, tenant_id, new_plan_code):
    if new_plan_code not in PLANS:
        raise HTTPException(400, f"Nomahlum plan: {new_plan_code}")
    sub = get_subscription(db, tenant_id)
    if not sub:
        return create_trial_subscription(db, tenant_id, new_plan_code)
    plan = get_plan(new_plan_code)
    now = datetime.utcnow()
    with transaction(db):
        sub.plan_code = new_plan_code
        sub.status = "ACTIVE"
        sub.current_period_start = now
        sub.current_period_end = now + timedelta(days=plan.billing_period_days)
        sub.cancelled_at = None
        if plan.price_tjs > 0:
            _issue_inv(db, sub, plan, now)
    db.refresh(sub)
    return sub


def cancel_subscription(db, tenant_id):
    sub = get_active_subscription(db, tenant_id)
    if not sub:
        raise HTTPException(404, "Faol obuna topilmadi")
    with transaction(db):
        sub.status = "CANCELLED"
        sub.cancelled_at = datetime.utcnow()
        sub.auto_renew = False
    db.refresh(sub)
    return sub


def mark_invoice_paid(db, invoice_id, tenant_id, method="BANK", *,
                      via, actor_user_id=None):
    if via == "DEMO" and not billing_settings.demo_enabled:
        raise HTTPException(403, "Demo payment o'chirilgan")
    inv = db.query(Invoice).filter(Invoice.id == invoice_id,
                                    Invoice.tenant_id == tenant_id).first()
    if not inv:
        raise HTTPException(404, "Invoice topilmadi")
    if inv.status == "PAID":
        raise HTTPException(400, "Allaqachon to'langan")
    with transaction(db):
        inv.status = "PAID"
        inv.paid_at = datetime.utcnow()
        inv.payment_method = method
        inv.note = (inv.note or "") + f" | via={via}"
        if actor_user_id:
            inv.note += f" actor={actor_user_id}"
        sub = db.query(Subscription).filter(Subscription.id == inv.subscription_id).first()
        if sub:
            sub.status = "ACTIVE"
        if via == "MANUAL":
            db.add(AuditLog(tenant_id=tenant_id, user_id=actor_user_id,
                            action="INVOICE_MANUAL_PAID", entity="Invoice",
                            entity_id=invoice_id,
                            payload={"method": method, "amount": inv.amount}))
    db.refresh(inv)
    return inv


def has_feature(db, tenant_id, feature):
    sub = get_active_subscription(db, tenant_id)
    if not sub:
        return False
    plan = PLANS.get(sub.plan_code)
    return bool(plan and plan.features.get(feature, False))


def within_limit(db, tenant_id, limit_name, current):
    sub = get_active_subscription(db, tenant_id)
    if not sub:
        return False
    plan = PLANS.get(sub.plan_code)
    if not plan:
        return False
    limit = plan.limits.get(limit_name)
    return True if limit is None else current < limit


def usage_snapshot(db, tenant_id):
    sub = get_active_subscription(db, tenant_id)
    plan = PLANS.get(sub.plan_code) if sub else None
    now = datetime.utcnow()
    ms = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    br = db.query(func.count(Branch.id)).filter(Branch.tenant_id == tenant_id).scalar() or 0
    emp = db.query(func.count(User.id)).filter(User.tenant_id == tenant_id).scalar() or 0
    pr = db.query(func.count(Product.id)).filter(Product.tenant_id == tenant_id,
                                                  Product.active.is_(True)).scalar() or 0
    inv = db.query(func.count(Sale.id)).filter(Sale.tenant_id == tenant_id,
                                                Sale.created_at >= ms).scalar() or 0
    pur = db.query(func.count(Purchase.id)).filter(Purchase.tenant_id == tenant_id,
                                                    Purchase.created_at >= ms).scalar() or 0
    items = [
        {"name": "branches", "current": br, "limit": plan.limits["branches"] if plan else None},
        {"name": "employees", "current": emp, "limit": plan.limits["employees"] if plan else None},
        {"name": "products", "current": pr, "limit": plan.limits["products"] if plan else None},
        {"name": "sales_this_month", "current": inv, "limit": plan.limits["invoices_per_month"] if plan else None},
        {"name": "purchases_this_month", "current": pur, "limit": plan.limits["purchases_per_month"] if plan else None}]
    for it in items:
        it["used_pct"] = None if it["limit"] is None else round(it["current"] / it["limit"] * 100, 1) if it["limit"] else 0.0
    return {"plan_code": sub.plan_code if sub else None,
            "status": sub.status if sub else None, "items": items}


def days_remaining(sub):
    now = datetime.utcnow()
    if sub.status == "TRIAL" and sub.trial_end:
        return max(0, (sub.trial_end - now).days)
    if sub.status in ("ACTIVE", "PAST_DUE"):
        return max(0, (sub.current_period_end - now).days)
    return None
'''

FILES["backend/app/billing/limits.py"] = '''from datetime import datetime
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.database import get_db
from app.core.deps import current_user
from app.models.user import User
from app.models.company import Branch
from app.models.product import Product
from app.models.sale import Sale
from app.models.supplier import Purchase
from app.billing.service import has_feature, within_limit, get_active_subscription
from app.billing.plans import PLANS


def require_feature(feature):
    def checker(user: User = Depends(current_user), db: Session = Depends(get_db)):
        if not has_feature(db, user.tenant_id, feature):
            raise HTTPException(402, f"Tarifingizda bu funksiya yoq: {feature}")
        return user
    return checker


def _c_branches(db, tid):
    return db.query(func.count(Branch.id)).filter(Branch.tenant_id == tid).scalar() or 0


def _c_employees(db, tid):
    return db.query(func.count(User.id)).filter(User.tenant_id == tid).scalar() or 0


def _c_products(db, tid):
    return db.query(func.count(Product.id)).filter(
        Product.tenant_id == tid, Product.active.is_(True)).scalar() or 0


def _c_sales(db, tid):
    ms = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return db.query(func.count(Sale.id)).filter(Sale.tenant_id == tid,
                                                 Sale.created_at >= ms).scalar() or 0


def _c_purch(db, tid):
    ms = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return db.query(func.count(Purchase.id)).filter(Purchase.tenant_id == tid,
                                                     Purchase.created_at >= ms).scalar() or 0


LIMIT_GETTERS = {"branches": _c_branches, "employees": _c_employees,
                 "products": _c_products, "invoices_per_month": _c_sales,
                 "purchases_per_month": _c_purch}


def enforce_limit(limit_name):
    if limit_name not in LIMIT_GETTERS:
        raise ValueError(f"Nomahlum limit: {limit_name}")
    def checker(user: User = Depends(current_user), db: Session = Depends(get_db)):
        cur = LIMIT_GETTERS[limit_name](db, user.tenant_id)
        if not within_limit(db, user.tenant_id, limit_name, cur):
            sub = get_active_subscription(db, user.tenant_id)
            plan = PLANS.get(sub.plan_code) if sub else None
            lim = plan.limits.get(limit_name) if plan else None
            raise HTTPException(402, f"Limit tugadi: {limit_name} ({cur}/{lim})")
        return user
    return checker
'''

FILES["backend/app/billing/api.py"] = '''from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.deps import current_user, require
from app.models.user import User
from app.billing.plans import list_plans, PLANS
from app.billing.schemas import SubscriptionOut, SubscribeIn, InvoiceOut, UsageOut
from app.billing.service import (get_or_create_subscription, change_plan,
                                  cancel_subscription, mark_invoice_paid,
                                  usage_snapshot, days_remaining)
from app.billing.models import Subscription, Invoice

router = APIRouter(prefix="/api/billing", tags=["billing"])


def _sub_d(sub):
    plan = PLANS.get(sub.plan_code)
    return {"id": sub.id, "tenant_id": sub.tenant_id, "plan_code": sub.plan_code,
            "plan": plan.to_dict() if plan else None, "status": sub.status,
            "started_at": sub.started_at, "trial_end": sub.trial_end,
            "current_period_start": sub.current_period_start,
            "current_period_end": sub.current_period_end,
            "cancelled_at": sub.cancelled_at, "auto_renew": sub.auto_renew,
            "days_remaining": days_remaining(sub)}


def _inv_d(inv):
    return {"id": inv.id, "number": inv.number, "tenant_id": inv.tenant_id,
            "plan_code": inv.plan_code, "amount": inv.amount,
            "currency": inv.currency, "status": inv.status,
            "period_start": inv.period_start, "period_end": inv.period_end,
            "issued_at": inv.issued_at, "due_at": inv.due_at,
            "paid_at": inv.paid_at, "payment_method": inv.payment_method}


@router.get("/plans")
def plans():
    return list_plans()


@router.get("/subscription", response_model=SubscriptionOut)
def my_sub(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _sub_d(get_or_create_subscription(db, user.tenant_id))


@router.post("/subscribe", response_model=SubscriptionOut)
def subscribe(data: SubscribeIn, user: User = Depends(require("finance", "edit")),
              db: Session = Depends(get_db)):
    return _sub_d(change_plan(db, user.tenant_id, data.plan_code))


@router.post("/cancel", response_model=SubscriptionOut)
def cancel(user: User = Depends(require("finance", "edit")),
           db: Session = Depends(get_db)):
    return _sub_d(cancel_subscription(db, user.tenant_id))


@router.get("/usage", response_model=UsageOut)
def usage(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return usage_snapshot(db, user.tenant_id)


@router.get("/invoices", response_model=list[InvoiceOut])
def invoices(user: User = Depends(require("finance", "view")),
             db: Session = Depends(get_db)):
    rows = (db.query(Invoice).filter(Invoice.tenant_id == user.tenant_id)
            .order_by(Invoice.issued_at.desc()).limit(100).all())
    return [_inv_d(i) for i in rows]


@router.post("/invoices/{invoice_id}/pay", response_model=InvoiceOut)
def pay_demo(invoice_id: int, method: str = "BANK",
             user: User = Depends(require("finance", "edit")),
             db: Session = Depends(get_db)):
    return _inv_d(mark_invoice_paid(db, invoice_id, user.tenant_id,
                                     method=method, via="DEMO",
                                     actor_user_id=user.id))


@router.post("/invoices/{invoice_id}/mark-paid-manual", response_model=InvoiceOut)
def manual(invoice_id: int, method: str = "BANK",
           user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role not in ("OWNER", "ADMIN"):
        raise HTTPException(403, "Faqat OWNER/ADMIN")
    return _inv_d(mark_invoice_paid(db, invoice_id, user.tenant_id,
                                     method=method, via="MANUAL",
                                     actor_user_id=user.id))


@router.post("/webhook/provider")
def webhook(payload: dict, db: Session = Depends(get_db)):
    raise HTTPException(501, "Provider webhook sozlanmagan")
'''

FILES["backend/app/notifications/__init__.py"] = "from app.notifications import models  # noqa\\n"

FILES["backend/app/notifications/models.py"] = '''from sqlalchemy import (Column, Integer, String, Text, Boolean, ForeignKey,
                        DateTime, JSON, UniqueConstraint, Index, func)
from app.core.database import Base


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_tenant_user", "tenant_id", "user_id"),
        Index("ix_notifications_tenant_read", "tenant_id", "read_at"),
    )
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    trigger_key = Column(String, nullable=False)
    severity = Column(String, nullable=False, default="info")
    title_tj = Column(String, nullable=False)
    body_tj = Column(Text, nullable=False)
    entity_type = Column(String, nullable=True)
    entity_id = Column(Integer, nullable=True)
    payload = Column(JSON, nullable=True)
    dedup_key = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    read_at = Column(DateTime, nullable=True)


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "trigger_key",
                                        "channel", name="uq_notif_pref"),)
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    trigger_key = Column(String, nullable=False)
    channel = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        Index("ix_notif_delivery_notification", "notification_id"),
        Index("ix_notif_delivery_status", "status", "attempted_at"),
    )
    id = Column(Integer, primary_key=True)
    notification_id = Column(Integer, ForeignKey("notifications.id"),
                             nullable=False, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    channel = Column(String, nullable=False)
    status = Column(String, nullable=False, default="PENDING")
    target = Column(String, nullable=True)
    error = Column(String, nullable=True)
    attempts = Column(Integer, default=1)
    attempted_at = Column(DateTime, server_default=func.now(), index=True)
    sent_at = Column(DateTime, nullable=True)
'''

FILES["backend/app/notifications/templates.py"] = '''TEMPLATES = {
    "billing.trial_ending": {"severity": "warning",
        "title_tj": "Sinab korish muddati tugaydi",
        "body_tj": "Sinab korish muddati {days} kundan keyin tugaydi.",
        "channels": ("in_app", "email")},
    "billing.subscription_expiry": {"severity": "warning",
        "title_tj": "Obuna muddati tugaydi",
        "body_tj": "Obunangiz {days} kundan keyin tugaydi (plan: {plan_code}).",
        "channels": ("in_app", "email")},
    "billing.invoice_overdue": {"severity": "critical",
        "title_tj": "Invoice tolanmagan",
        "body_tj": "Invoice #{number} ({amount} TJS) muddati otdi.",
        "channels": ("in_app", "email")},
    "forecast.stockout_critical": {"severity": "critical",
        "title_tj": "Mahsulot tugash arafasida",
        "body_tj": "{name} - {days} kun ichida tugaydi ({stock} dona, {velocity}/kun).",
        "channels": ("in_app", "telegram")},
    "variance.high_loss": {"severity": "critical",
        "title_tj": "Inventarizatsiyada katta yoqotish",
        "body_tj": "{branch_name} - {name}: {qty} dona yoqotildi ({value} TJS).",
        "channels": ("in_app", "telegram")},
    "variance.recurring": {"severity": "warning",
        "title_tj": "Takrorlanuvchi yoqotish",
        "body_tj": "{name} - {branch_name}: {events} marta yoqotish (jami {value} TJS).",
        "channels": ("in_app",)},
    "supplier_anomaly.upward_trend": {"severity": "warning",
        "title_tj": "Supplier narxi osib bormoqda",
        "body_tj": "{name} - {first} -> {last} (+{pct}%).",
        "channels": ("in_app", "email")},
    "supplier_anomaly.supplier_gap": {"severity": "opportunity",
        "title_tj": "Supplier narxi bozordan qimmat",
        "body_tj": "{name}: {supplier_name} {pct}% qimmat.",
        "channels": ("in_app",)},
}


def get_template(k):
    return TEMPLATES.get(k)


def list_templates():
    return [{"key": k, "severity": v["severity"],
             "title_tj": v["title_tj"],
             "default_channels": list(v["channels"])} for k, v in TEMPLATES.items()]


def render_notification(k, payload, locale="tj"):
    tpl = TEMPLATES.get(k)
    if not tpl:
        return k, ""
    try:
        body = tpl["body_tj"].format(**payload)
    except (KeyError, IndexError):
        body = tpl["body_tj"]
    return tpl["title_tj"], body
'''

FILES["backend/app/notifications/throttle.py"] = '''import hashlib
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.notifications.models import Notification


WINDOWS = {"billing.trial_ending": 24, "billing.subscription_expiry": 24,
           "billing.invoice_overdue": 48, "forecast.stockout_critical": 12,
           "variance.high_loss": 168, "variance.recurring": 168,
           "supplier_anomaly.upward_trend": 168, "supplier_anomaly.supplier_gap": 336}


def make_dedup_key(trigger_key, entity_type, entity_id):
    return f"{trigger_key}:{entity_type or '-'}:{entity_id or 0}"


def _lk(tid, dk):
    h = hashlib.blake2b(dk.encode(), digest_size=8).digest()
    return (tid << 32) ^ (int.from_bytes(h, "big") & 0x7FFFFFFFFFFFFFFF)


def acquire_dedup_lock(db, tid, dk):
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _lk(tid, dk)})


def is_throttled(db, tid, dk, trigger_key):
    since = datetime.utcnow() - timedelta(hours=WINDOWS.get(trigger_key, 24))
    return db.query(Notification.id).filter(
        Notification.tenant_id == tid, Notification.dedup_key == dk,
        Notification.created_at >= since).first() is not None
'''

FILES["backend/app/notifications/preferences.py"] = '''from sqlalchemy.orm import Session
from app.notifications.models import NotificationPreference
from app.notifications.templates import get_template


def get_enabled_channels(db, tid, uid, tk):
    rows = (db.query(NotificationPreference).filter(
        NotificationPreference.tenant_id == tid,
        NotificationPreference.user_id == uid,
        NotificationPreference.trigger_key.in_([tk, "*"])).all())
    if not rows:
        tpl = get_template(tk)
        return list(tpl["channels"]) if tpl else ["in_app"]
    spec = [r for r in rows if r.trigger_key == tk]
    wild = [r for r in rows if r.trigger_key == "*"]
    src = spec or wild
    enabled = [r.channel for r in src if r.enabled]
    return enabled or ["in_app"]


def set_preference(db, tid, uid, tk, ch, enabled):
    row = (db.query(NotificationPreference).filter(
        NotificationPreference.tenant_id == tid,
        NotificationPreference.user_id == uid,
        NotificationPreference.trigger_key == tk,
        NotificationPreference.channel == ch).first())
    if row:
        row.enabled = enabled
    else:
        row = NotificationPreference(tenant_id=tid, user_id=uid,
                                      trigger_key=tk, channel=ch,
                                      enabled=enabled)
        db.add(row)
    db.commit(); db.refresh(row)
    return row


def list_preferences(db, tid, uid):
    rows = (db.query(NotificationPreference).filter(
        NotificationPreference.tenant_id == tid,
        NotificationPreference.user_id == uid).all())
    return [{"trigger_key": r.trigger_key, "channel": r.channel,
             "enabled": r.enabled} for r in rows]
'''

FILES["backend/app/notifications/service.py"] = '''import logging
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.user import User
from app.notifications.models import Notification, NotificationDelivery
from app.notifications.templates import get_template, render_notification
from app.notifications.throttle import make_dedup_key, is_throttled, acquire_dedup_lock
from app.notifications.preferences import get_enabled_channels
from app.core.tx import transaction

logger = logging.getLogger(__name__)


def emit(db, tenant_id, trigger_key, payload, entity_type=None,
         entity_id=None, user_ids=None, locale="tj"):
    tpl = get_template(trigger_key)
    if tpl is None:
        logger.warning("Unknown trigger: %s", trigger_key)
        return None
    dk = make_dedup_key(trigger_key, entity_type, entity_id)
    if user_ids is None:
        user_ids = [r[0] for r in db.query(User.id).filter(
            User.tenant_id == tenant_id,
            User.role.in_(["OWNER", "ADMIN"])).all()]
    if not user_ids:
        return None
    title, body = render_notification(trigger_key, payload, locale)
    with transaction(db):
        acquire_dedup_lock(db, tenant_id, dk)
        if is_throttled(db, tenant_id, dk, trigger_key):
            return None
        n = Notification(tenant_id=tenant_id, user_id=None,
                         trigger_key=trigger_key, severity=tpl["severity"],
                         title_tj=title, body_tj=body,
                         entity_type=entity_type, entity_id=entity_id,
                         payload=payload, dedup_key=dk)
        db.add(n); db.flush()
        for uid in user_ids:
            user = db.query(User).filter(User.id == uid,
                                          User.tenant_id == tenant_id).first()
            if not user:
                continue
            chans = get_enabled_channels(db, tenant_id, uid, trigger_key)
            for ch in chans:
                if ch == "in_app":
                    db.add(NotificationDelivery(
                        notification_id=n.id, tenant_id=tenant_id,
                        channel="in_app", status="SENT",
                        target=f"user:{uid}", sent_at=datetime.utcnow()))
                else:
                    db.add(NotificationDelivery(
                        notification_id=n.id, tenant_id=tenant_id,
                        channel=ch, status="PENDING", target=None))
    db.refresh(n)
    return n


def emit_bulk(db, tid, events):
    c = 0
    for ev in events:
        try:
            if emit(db, tid, **ev):
                c += 1
        except Exception:
            logger.exception("emit failed: %s", ev.get("trigger_key"))
    return c
'''

FILES["backend/app/notifications/dispatcher.py"] = '''import logging
from datetime import datetime
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.core.tx import transaction
from app.models.user import User
from app.notifications.models import Notification, NotificationDelivery
from app.notifications.channels import CHANNELS

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
BATCH = 100


def dispatch_pending(db=None):
    own = db is None
    if own:
        db = SessionLocal()
    sent = failed = skipped = 0
    try:
        rows = (db.query(NotificationDelivery)
                .filter(NotificationDelivery.status == "PENDING",
                        NotificationDelivery.attempts < MAX_ATTEMPTS)
                .order_by(NotificationDelivery.attempted_at.asc())
                .limit(BATCH).all())
        if not rows:
            return {"sent": 0, "failed": 0, "skipped": 0}
        jobs = []
        for d in rows:
            n = db.query(Notification).filter(Notification.id == d.notification_id).first()
            if not n:
                continue
            u = (db.query(User).filter(User.tenant_id == d.tenant_id,
                                        User.role.in_(["OWNER", "ADMIN"]))
                 .order_by(User.id.asc()).first())
            jobs.append({"delivery_id": d.id, "channel": d.channel,
                         "notification": n, "user": u,
                         "attempts": d.attempts or 1})
        db.expunge_all()
        db.commit()
        results = []
        for j in jobs:
            ch = CHANNELS.get(j["channel"])
            if ch is None or not ch.is_available():
                results.append({"delivery_id": j["delivery_id"],
                                "status": "SKIPPED", "target": None,
                                "error": f"channel {j['channel']} unavailable",
                                "attempts": j["attempts"]})
                continue
            tgt = ch.resolve_target(j["user"])
            if not tgt:
                results.append({"delivery_id": j["delivery_id"],
                                "status": "SKIPPED", "target": None,
                                "error": "no target", "attempts": j["attempts"]})
                continue
            try:
                r = ch.send(j["notification"], j["user"], tgt)
                results.append({"delivery_id": j["delivery_id"],
                                "status": r.status, "target": r.target,
                                "error": r.error, "attempts": j["attempts"]})
            except Exception as e:
                logger.exception("dispatch send failed")
                results.append({"delivery_id": j["delivery_id"],
                                "status": "FAILED", "target": tgt,
                                "error": str(e)[:200], "attempts": j["attempts"]})
        with transaction(db):
            for r in results:
                d = db.query(NotificationDelivery).filter(
                    NotificationDelivery.id == r["delivery_id"]).first()
                if not d:
                    continue
                d.status = r["status"]
                d.target = r["target"]
                d.error = r["error"]
                d.attempts = (r["attempts"] or 1) + (1 if r["status"] == "FAILED" else 0)
                d.attempted_at = datetime.utcnow()
                if r["status"] == "SENT":
                    d.sent_at = datetime.utcnow()
                    sent += 1
                elif r["status"] == "SKIPPED":
                    skipped += 1
                else:
                    failed += 1
        return {"sent": sent, "failed": failed, "skipped": skipped}
    finally:
        if own:
            db.close()


def retry_failed(db=None):
    own = db is None
    if own:
        db = SessionLocal()
    try:
        n = (db.query(NotificationDelivery)
             .filter(NotificationDelivery.status == "FAILED",
                     NotificationDelivery.attempts < MAX_ATTEMPTS)
             .update({"status": "PENDING"}, synchronize_session=False))
        db.commit()
        return {"requeued": int(n)}
    finally:
        if own:
            db.close()
'''

FILES["backend/app/notifications/triggers.py"] = '''from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models.company import Branch
from app.billing.models import Subscription, Invoice
from app.notifications.service import emit_bulk


def scan_billing(db, tid):
    events = []
    now = datetime.utcnow()
    for s in db.query(Subscription).filter(
            Subscription.tenant_id == tid, Subscription.status == "TRIAL",
            Subscription.trial_end.isnot(None)).all():
        if not s.trial_end:
            continue
        d = (s.trial_end - now).days
        if 0 <= d <= 3:
            events.append({"trigger_key": "billing.trial_ending",
                           "payload": {"days": d, "plan_code": s.plan_code},
                           "entity_type": "Subscription", "entity_id": s.id})
    for s in db.query(Subscription).filter(
            Subscription.tenant_id == tid, Subscription.status == "ACTIVE").all():
        if not s.current_period_end:
            continue
        d = (s.current_period_end - now).days
        if 0 <= d <= 3:
            events.append({"trigger_key": "billing.subscription_expiry",
                           "payload": {"days": d, "plan_code": s.plan_code},
                           "entity_type": "Subscription", "entity_id": s.id})
    for inv in db.query(Invoice).filter(
            Invoice.tenant_id == tid, Invoice.status.in_(["SENT", "OVERDUE"]),
            Invoice.due_at.isnot(None), Invoice.due_at < now).all():
        if not inv.due_at:
            continue
        events.append({"trigger_key": "billing.invoice_overdue",
                       "payload": {"number": inv.number,
                                   "amount": f"{inv.amount:,.0f}"},
                       "entity_type": "Invoice", "entity_id": inv.id})
    return events


def scan_forecast(db, tid, bid):
    from app.intelligence.forecast import stockout_risk, ForecastConfig
    events = []
    cfg = ForecastConfig()
    for r in stockout_risk(db, tid, bid, None, cfg):
        if r["risk"] != "critical" or (r.get("days_until_stockout") or 999) > 3:
            continue
        events.append({"trigger_key": "forecast.stockout_critical",
                       "payload": {"name": r["name"],
                                   "days": r.get("days_until_stockout"),
                                   "stock": r.get("current_stock"),
                                   "velocity": r.get("velocity")},
                       "entity_type": "Product", "entity_id": r["product_id"]})
    return events


def scan_variance(db, tid, days=7):
    from app.intelligence.variance import analyze, VarianceConfig
    events = []
    now = datetime.utcnow()
    since = now - timedelta(days=days)
    r = analyze(db, tid, since, now, branch_id=None, cfg=VarianceConfig())
    for it in r["items"]:
        if it["severity"] != "critical":
            continue
        events.append({"trigger_key": "variance.high_loss",
                       "payload": {"name": it["name"],
                                   "branch_name": it.get("branch_name") or "-",
                                   "qty": it["variance_qty"],
                                   "value": f"{abs(it['variance_value']):,.0f}"},
                       "entity_type": "InventoryCount", "entity_id": it["count_id"]})
    for x in r["recurring_offenders"]:
        events.append({"trigger_key": "variance.recurring",
                       "payload": {"name": x["name"],
                                   "branch_name": x.get("branch_name") or "-",
                                   "events": x["events"],
                                   "value": f"{x['total_lost_value']:,.0f}"},
                       "entity_type": "Product", "entity_id": x["product_id"]})
    return events


def scan_supplier(db, tid):
    from app.intelligence.supplier_anomaly import analyze, AnomalyConfig
    events = []
    r = analyze(db, tid, AnomalyConfig())
    for p in r["products"]:
        for f in p["findings"]:
            if f["kind"] == "upward_trend":
                events.append({"trigger_key": "supplier_anomaly.upward_trend",
                               "payload": {"name": p["name"],
                                           "first": f["first_price"],
                                           "last": f["last_price"],
                                           "pct": f["pct_change"]},
                               "entity_type": "Product",
                               "entity_id": p["product_id"]})
            elif f["kind"] == "supplier_gap" and f["impact"] >= 500:
                events.append({"trigger_key": "supplier_anomaly.supplier_gap",
                               "payload": {"name": p["name"],
                                           "supplier_name": f"#{f['supplier_id']}",
                                           "pct": f["pct_vs_market"],
                                           "impact": f"{f['impact']:,.0f}"},
                               "entity_type": "Product",
                               "entity_id": p["product_id"]})
    return events


def scan_all(db, tid, branch_ids=None):
    if branch_ids is None:
        branch_ids = [b.id for b in db.query(Branch).filter(
            Branch.tenant_id == tid).all()]
    all_events = []
    all_events += scan_billing(db, tid)
    for bid in branch_ids:
        all_events += scan_forecast(db, tid, bid)
    all_events += scan_variance(db, tid)
    all_events += scan_supplier(db, tid)
    emitted = emit_bulk(db, tid, all_events)
    return {"scanned_events": len(all_events),
            "emitted_notifications": emitted,
            "throttled": len(all_events) - emitted}
'''

FILES["backend/app/notifications/scheduler.py"] = '''import logging
from app.core.database import SessionLocal
from app.models.tenant import Tenant
from app.notifications.triggers import scan_all
from app.notifications.dispatcher import dispatch_pending

logger = logging.getLogger(__name__)


def run_once():
    db = SessionLocal()
    total = 0
    tenants_count = 0
    try:
        tenants = db.query(Tenant).filter(Tenant.active.is_(True)).all()
        tenants_count = len(tenants)
        for t in tenants:
            try:
                r = scan_all(db, t.id)
                total += r["emitted_notifications"]
            except Exception:
                logger.exception("Tenant %s scan failed", t.id)
    finally:
        db.close()
    disp = dispatch_pending()
    return {"tenants": tenants_count, "emitted": total, **disp}
'''

FILES["backend/app/notifications/api.py"] = '''from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.database import get_db
from app.core.deps import current_user, require
from app.models.user import User
from app.notifications.models import Notification, NotificationDelivery
from app.notifications.templates import list_templates
from app.notifications.preferences import set_preference, list_preferences
from app.notifications.triggers import scan_all
from app.notifications.dispatcher import dispatch_pending

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("/templates")
def templates():
    return list_templates()


@router.get("")
def list_notifs(limit: int = 50, unread_only: bool = False,
                user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    q = db.query(Notification).filter(Notification.tenant_id == user.tenant_id)
    if unread_only:
        q = q.filter(Notification.read_at.is_(None))
    rows = q.order_by(Notification.created_at.desc()).limit(limit).all()
    return [_d(n, db) for n in rows]


@router.get("/unread-count")
def unread(user: User = Depends(current_user), db: Session = Depends(get_db)):
    n = (db.query(func.count(Notification.id)).filter(
        Notification.tenant_id == user.tenant_id,
        Notification.read_at.is_(None)).scalar() or 0)
    return {"count": int(n)}


@router.post("/{notification_id}/read")
def mark_read(notification_id: int, user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    n = db.query(Notification).filter(Notification.id == notification_id,
                                       Notification.tenant_id == user.tenant_id).first()
    if not n:
        raise HTTPException(404, "Topilmadi")
    if n.read_at is None:
        n.read_at = datetime.utcnow()
        db.commit()
    return {"id": n.id, "read_at": n.read_at}


@router.post("/read-all")
def read_all(user: User = Depends(current_user), db: Session = Depends(get_db)):
    now = datetime.utcnow()
    n = db.query(Notification).filter(Notification.tenant_id == user.tenant_id,
                                       Notification.read_at.is_(None))\
        .update({"read_at": now}, synchronize_session=False)
    db.commit()
    return {"marked": int(n)}


class PrefIn(BaseModel):
    trigger_key: str = Field(min_length=1, max_length=64)
    channel: str = Field(pattern="^(in_app|telegram|email)$")
    enabled: bool


@router.get("/preferences")
def get_prefs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return list_preferences(db, user.tenant_id, user.id)


@router.post("/preferences")
def set_pref(data: PrefIn, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    row = set_preference(db, user.tenant_id, user.id,
                         data.trigger_key, data.channel, data.enabled)
    return {"trigger_key": row.trigger_key, "channel": row.channel,
            "enabled": row.enabled}


@router.post("/scan")
def scan(user: User = Depends(require("intelligence", "view")),
         db: Session = Depends(get_db)):
    if user.role not in ("OWNER", "ADMIN"):
        raise HTTPException(403, "Faqat OWNER/ADMIN")
    return scan_all(db, user.tenant_id)


@router.post("/dispatch")
def dispatch(user: User = Depends(require("intelligence", "view")),
             db: Session = Depends(get_db)):
    if user.role not in ("OWNER", "ADMIN"):
        raise HTTPException(403, "Faqat OWNER/ADMIN")
    return dispatch_pending()


def _d(n, db):
    ds = db.query(NotificationDelivery).filter(
        NotificationDelivery.notification_id == n.id).all()
    return {"id": n.id, "trigger_key": n.trigger_key, "severity": n.severity,
            "title_tj": n.title_tj, "body_tj": n.body_tj,
            "entity_type": n.entity_type, "entity_id": n.entity_id,
            "payload": n.payload, "created_at": n.created_at,
            "read_at": n.read_at,
            "deliveries": [{"channel": d.channel, "status": d.status,
                            "target": d.target, "error": d.error} for d in ds]}
'''

FILES["backend/app/notifications/channels/__init__.py"] = '''from app.notifications.channels.in_app import InAppChannel
from app.notifications.channels.telegram import TelegramChannel
from app.notifications.channels.email import EmailChannel

CHANNELS = {"in_app": InAppChannel(), "telegram": TelegramChannel(),
            "email": EmailChannel()}
'''

FILES["backend/app/notifications/channels/base.py"] = '''from dataclasses import dataclass


@dataclass
class DeliveryResult:
    status: str
    target: str | None = None
    error: str | None = None


class BaseChannel:
    name = "base"
    def is_available(self): return False
    def resolve_target(self, user): return None
    def send(self, notification, user, target):
        raise NotImplementedError
'''

FILES["backend/app/notifications/channels/in_app.py"] = '''from app.notifications.channels.base import BaseChannel, DeliveryResult


class InAppChannel(BaseChannel):
    name = "in_app"
    def is_available(self): return True
    def resolve_target(self, user): return f"user:{user.id}" if user else None
    def send(self, notification, user, target):
        return DeliveryResult("SENT", target or "in_app")
'''

FILES["backend/app/notifications/channels/telegram.py"] = '''import os, logging, urllib.request, urllib.parse
from app.notifications.channels.base import BaseChannel, DeliveryResult

logger = logging.getLogger(__name__)


class TelegramChannel(BaseChannel):
    name = "telegram"
    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    def is_available(self): return bool(self.token)
    def resolve_target(self, user): return None
    def send(self, notification, user, target):
        if not self.is_available():
            return DeliveryResult("SKIPPED", error="telegram not configured")
        if not target:
            return DeliveryResult("SKIPPED", error="no chat_id")
        try:
            text = f"*{notification.title_tj}*\\n\\n{notification.body_tj}"
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": target, "text": text,
                                            "parse_mode": "Markdown"}).encode()
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return DeliveryResult("SENT", target=target)
                return DeliveryResult("FAILED", target=target,
                                       error=f"HTTP {resp.status}")
        except Exception as e:
            logger.exception("Telegram failed")
            return DeliveryResult("FAILED", target=target, error=str(e)[:200])
'''

FILES["backend/app/notifications/channels/email.py"] = '''import os, logging, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.notifications.channels.base import BaseChannel, DeliveryResult

logger = logging.getLogger(__name__)


class EmailChannel(BaseChannel):
    name = "email"
    def __init__(self):
        self.host = os.getenv("SMTP_HOST", "").strip()
        self.port = int(os.getenv("SMTP_PORT", "587") or 587)
        self.user = os.getenv("SMTP_USER", "").strip()
        self.password = os.getenv("SMTP_PASSWORD", "").strip()
        self.sender = os.getenv("SMTP_FROM", self.user).strip()
    def is_available(self): return bool(self.host and self.sender)
    def resolve_target(self, user): return getattr(user, "email", None) if user else None
    def send(self, notification, user, target):
        if not self.is_available():
            return DeliveryResult("SKIPPED", error="email not configured")
        if not target:
            return DeliveryResult("SKIPPED", error="no email")
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = notification.title_tj
            msg["From"] = self.sender
            msg["To"] = target
            msg.attach(MIMEText(notification.body_tj, "plain", "utf-8"))
            with smtplib.SMTP(self.host, self.port, timeout=10) as s:
                s.starttls()
                if self.user:
                    s.login(self.user, self.password)
                s.sendmail(self.sender, [target], msg.as_string())
            return DeliveryResult("SENT", target=target)
        except Exception as e:
            logger.exception("Email failed")
            return DeliveryResult("FAILED", target=target, error=str(e)[:200])
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
    print(f"\nPART A: {count} ta fayl")
    subprocess.run(["git", "add", "-A"], check=False)
    subprocess.run(["git", "commit", "-m", "PART A: intelligence rest + AI + main + billing + notifications"], check=False)
    subprocess.run(["git", "push", "origin", "main"], check=False)
    print("GitHub'ga yuborildi")


if __name__ == "__main__":
    main()
