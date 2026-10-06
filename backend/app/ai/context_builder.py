from dataclasses import dataclass, asdict
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
        out["top_actions_tj"] = "\n".join([f"{i+1}. [{a['priority'].upper()}] {a['action']} - {a.get('name')} - {_fmt(a['impact'], cur)}" for i, a in enumerate(top)])
    return out
