from datetime import datetime
from app.models.tenant import Tenant
from app.reports.types import Report, Section, Column
from app.intelligence.profit import real_profit, profit_by_branch


def build_pnl(db, tenant_id, since, until, branch_id=None):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    cur = tenant.currency if tenant else "TJS"
    p = real_profit(db, tenant_id, since, until, branch_id=branch_id)
    rpt = Report(key="pnl", title_tj="Foyda va zarar hisoboti (P&L)",
                 subtitle_tj=tenant.name if tenant else None,
                 meta={"currency": cur, "generated_at": datetime.utcnow().isoformat()})
    rpt.add(Section(title_tj="Moliyaviy korsatkichlar",
                    columns=[Column("item", "Korsatkich", width=220),
                             Column("value", "Summa", align="right", width=160, format="money")],
                    rows=[{"item": "Tushum", "value": p["revenue"]},
                          {"item": "COGS", "value": p["cogs"]},
                          {"item": "Yalpi foyda", "value": p["gross_profit"]},
                          {"item": "Xarajat", "value": p["expenses"]},
                          {"item": "Sof foyda", "value": p["net_profit"]}]))
    rpt.add(Section(title_tj="Marja va KPI",
                    columns=[Column("item", "Korsatkich", width=220),
                             Column("value", "Qiymat", align="right")],
                    rows=[{"item": "Yalpi marja", "value": f"{p['gross_margin_pct']:.2f}%"},
                          {"item": "Sof marja", "value": f"{p['net_margin_pct']:.2f}%"},
                          {"item": "Sotuvlar soni", "value": f"{p['sales_count']:,}"}]))
    branches = profit_by_branch(db, tenant_id, since, until)
    if len(branches) > 1:
        rpt.add(Section(title_tj="Filiallar boyicha",
                        columns=[Column("name", "Filial"),
                                 Column("revenue", "Tushum", align="right", format="money"),
                                 Column("gross_profit", "Yalpi", align="right", format="money"),
                                 Column("net_profit", "Sof", align="right", format="money")],
                        rows=[{"name": b["branch_name"], "revenue": b["revenue"],
                               "gross_profit": b["gross_profit"],
                               "net_profit": b["net_profit"]} for b in branches]))
    return rpt
