from dataclasses import dataclass, asdict
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
