from datetime import datetime
from app.models.tenant import Tenant
from app.reports.types import Report, Section, Column
from app.intelligence.dead_stock_tiers import classify, TiersConfig


def build_dead_stock(db, tenant_id, since, until, branch_id=None):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    cur = tenant.currency if tenant else "TJS"
    r = classify(db, tenant_id, branch_id=branch_id, cfg=TiersConfig(),
                 include_normal=False)
    rpt = Report(key="dead_stock", title_tj="Qotib qolgan zaxira",
                 subtitle_tj=tenant.name if tenant else None,
                 meta={"currency": cur, "generated_at": datetime.utcnow().isoformat()})
    s = r["summary"]
    rpt.add(Section(title_tj="Umumiy",
                    columns=[Column("tier", "Kategoriya"),
                             Column("count", "Mahsulot", align="right", format="int"),
                             Column("capital_locked", "Kapital", align="right", format="money")],
                    rows=[{"tier": t, "count": s["by_tier"][t]["count"],
                           "capital_locked": s["by_tier"][t]["capital_locked"]}
                          for t in ("DEAD", "SLOW", "AT-RISK", "NORMAL")]))
    for tier in ("DEAD", "SLOW"):
        items = [it for it in r["items"] if it["tier"] == tier]
        if not items:
            continue
        rpt.add(Section(title_tj=f"{tier} mahsulotlar",
                        columns=[Column("sku", "SKU"), Column("name", "Mahsulot"),
                                 Column("quantity", "Miqdor", align="right", format="float"),
                                 Column("capital_locked", "Kapital", align="right", format="money"),
                                 Column("last_sale_days", "Oxirgi sotuv (kun)",
                                        align="right", format="int")],
                        rows=items))
    return rpt
