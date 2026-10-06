from datetime import datetime
from app.models.tenant import Tenant
from app.reports.types import Report, Section, Column
from app.intelligence.supplier_anomaly import analyze, AnomalyConfig


def build_supplier(db, tenant_id, since, until, branch_id=None):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    cur = tenant.currency if tenant else "TJS"
    cfg = AnomalyConfig()
    r = analyze(db, tenant_id, cfg)
    rpt = Report(key="supplier", title_tj="Supplier narx anomaliyalari",
                 subtitle_tj=tenant.name if tenant else None,
                 meta={"currency": cur, "generated_at": datetime.utcnow().isoformat()})
    s = r["summary"]
    rpt.add(Section(title_tj=f"Umumiy (oyna: {cfg.window_days} kun)",
                    columns=[Column("item", "Korsatkich"),
                             Column("value", "Qiymat", align="right")],
                    rows=[{"item": "Tekshirilgan", "value": f"{s['products_analyzed']:,}"},
                          {"item": "Anomaliya", "value": f"{s['products_with_anomalies']:,}"},
                          {"item": "Supplierlar", "value": f"{s['suppliers_flagged']:,}"},
                          {"item": "Umumiy tasir",
                           "value": f"{s['total_estimated_impact']:,.2f} {cur}"}]))
    if r["suppliers"]:
        rpt.add(Section(title_tj="Supplier reytingi",
                        columns=[Column("supplier_name", "Supplier", width=200),
                                 Column("anomaly_count", "Anomaliya",
                                        align="right", format="int"),
                                 Column("estimated_impact", "Tasir",
                                        align="right", format="money")],
                        rows=[{"supplier_name": x["supplier_name"] or f"#{x['supplier_id']}",
                               "anomaly_count": x["anomaly_count"],
                               "estimated_impact": x["estimated_impact"]}
                              for x in r["suppliers"]]))
    return rpt
