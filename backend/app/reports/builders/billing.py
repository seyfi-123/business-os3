from datetime import datetime
from app.models.tenant import Tenant
from app.reports.types import Report, Section, Column
from app.billing.models import Invoice, Subscription
from app.billing.plans import PLANS


def build_billing(db, tenant_id, since, until, branch_id=None):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    cur = tenant.currency if tenant else "TJS"
    sub = (db.query(Subscription).filter(Subscription.tenant_id == tenant_id)
           .order_by(Subscription.id.desc()).first())
    invoices = (db.query(Invoice).filter(Invoice.tenant_id == tenant_id,
                                          Invoice.issued_at >= since,
                                          Invoice.issued_at < until)
                .order_by(Invoice.issued_at.desc()).limit(500).all())
    rpt = Report(key="billing", title_tj="Obuna va tolovlar",
                 subtitle_tj=tenant.name if tenant else None,
                 meta={"currency": cur, "generated_at": datetime.utcnow().isoformat()})
    if sub:
        plan = PLANS.get(sub.plan_code)
        rpt.add(Section(title_tj="Joriy obuna",
                        columns=[Column("item", "Korsatkich"),
                                 Column("value", "Qiymat", align="right")],
                        rows=[{"item": "Plan",
                               "value": plan.name_tj if plan else sub.plan_code},
                              {"item": "Holat", "value": sub.status}]))
    rpt.add(Section(title_tj="Invoicelar",
                    columns=[Column("number", "Raqam"),
                             Column("plan_code", "Plan"),
                             Column("amount", "Summa", align="right", format="money"),
                             Column("status", "Holat")],
                    rows=[{"number": i.number, "plan_code": i.plan_code,
                           "amount": i.amount, "status": i.status} for i in invoices]))
    return rpt
