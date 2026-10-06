from datetime import datetime
from app.models.tenant import Tenant
from app.reports.types import Report, Section, Column
from app.intelligence.forecast import (revenue_forecast, stockout_risk,
                                        excess_stock_risk, ForecastConfig)


def build_forecast(db, tenant_id, since, until, branch_id=None):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    cur = tenant.currency if tenant else "TJS"
    cfg = ForecastConfig()
    rev = revenue_forecast(db, tenant_id, branch_id, cfg)
    stockout = stockout_risk(db, tenant_id, branch_id, None, cfg) if branch_id else []
    excess = excess_stock_risk(db, tenant_id, branch_id, None, cfg) if branch_id else []
    rpt = Report(key="forecast", title_tj="Prognoz hisoboti",
                 subtitle_tj=tenant.name if tenant else None,
                 meta={"currency": cur, "generated_at": datetime.utcnow().isoformat()})
    if rev.get("available"):
        rpt.add(Section(title_tj=f"Tushum prognozi (tarix: {cfg.history_days} kun)",
                        columns=[Column("horizon", "Davr"),
                                 Column("total", "Umumiy", align="right", format="money")],
                        rows=[{"horizon": "7 kun", "total": rev["forecast"]["7d"]["total"]},
                              {"horizon": "14 kun", "total": rev["forecast"]["14d"]["total"]},
                              {"horizon": "30 kun", "total": rev["forecast"]["30d"]["total"]}]))
    if stockout:
        rpt.add(Section(title_tj="Stockout xavfi",
                        columns=[Column("name", "Mahsulot", width=200),
                                 Column("current_stock", "Zaxira", align="right", format="float"),
                                 Column("days_until_stockout", "Kungacha",
                                        align="right", format="float"),
                                 Column("risk", "Xavf")],
                        rows=stockout[:30]))
    if excess:
        rpt.add(Section(title_tj="Ortiqcha zaxira",
                        columns=[Column("name", "Mahsulot", width=200),
                                 Column("days_of_cover", "Qoplash (kun)",
                                        align="right", format="float"),
                                 Column("capital_locked", "Kapital",
                                        align="right", format="money"),
                                 Column("risk", "Xavf")],
                        rows=excess[:30]))
    return rpt
