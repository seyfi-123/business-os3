from datetime import datetime, timedelta
from app.models.tenant import Tenant
from app.reports.types import Report, Section, Column
from app.intelligence.variance import analyze, VarianceConfig


def build_variance(db, tenant_id, since, until, branch_id=None):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    cur = tenant.currency if tenant else "TJS"
    r = analyze(db, tenant_id, since, until - timedelta(microseconds=1),
                branch_id=branch_id, cfg=VarianceConfig())
    rpt = Report(key="variance", title_tj="Inventarizatsiya farqi",
                 subtitle_tj=tenant.name if tenant else None,
                 meta={"currency": cur, "generated_at": datetime.utcnow().isoformat()})
    s = r["summary"]
    rpt.add(Section(title_tj="Umumiy",
                    columns=[Column("item", "Korsatkich"),
                             Column("value", "Qiymat", align="right")],
                    rows=[{"item": "Sanoq", "value": f"{s['total_counts']:,}"},
                          {"item": "Yoqotish (qiymat)",
                           "value": f"{s['loss_value']:,.2f} {cur}"},
                          {"item": "Ortiqcha (qiymat)",
                           "value": f"{s['surplus_value']:,.2f} {cur}"},
                          {"item": "Sof yoqotish",
                           "value": f"{s['net_loss_value']:,.2f} {cur}"},
                          {"item": "Recurring",
                           "value": f"{s['recurring_offenders']:,}"}]))
    if r["recurring_offenders"]:
        rpt.add(Section(title_tj="Takrorlanuvchi yoqotishlar",
                        columns=[Column("name", "Mahsulot", width=200),
                                 Column("branch_name", "Filial"),
                                 Column("events", "Marta", align="right", format="int"),
                                 Column("total_lost_value", "Qiymat",
                                        align="right", format="money")],
                        rows=r["recurring_offenders"]))
    return rpt
