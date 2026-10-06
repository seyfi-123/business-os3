from dataclasses import dataclass, asdict
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
