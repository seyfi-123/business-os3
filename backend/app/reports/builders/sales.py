from datetime import datetime
from sqlalchemy import func
from app.models.tenant import Tenant
from app.models.sale import Sale, SaleItem
from app.models.product import Product
from app.reports.types import Report, Section, Column


def build_sales(db, tenant_id, since, until, branch_id=None):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    cur = tenant.currency if tenant else "TJS"
    q = (db.query(func.date(Sale.created_at).label("day"),
                  func.count(Sale.id).label("count"),
                  func.coalesce(func.sum(Sale.total), 0).label("revenue"),
                  func.coalesce(func.sum(Sale.discount), 0).label("discount"))
         .filter(Sale.tenant_id == tenant_id, Sale.created_at >= since,
                 Sale.created_at < until))
    if branch_id:
        q = q.filter(Sale.branch_id == branch_id)
    rows = q.group_by(func.date(Sale.created_at)).order_by(func.date(Sale.created_at).desc()).all()
    rpt = Report(key="sales", title_tj="Sotuv hisoboti",
                 subtitle_tj=tenant.name if tenant else None,
                 meta={"currency": cur, "generated_at": datetime.utcnow().isoformat()})
    rpt.add(Section(title_tj="Kunlik sotuvlar",
                    columns=[Column("day", "Sana"),
                             Column("count", "Sotuv", align="right", format="int"),
                             Column("revenue", "Tushum", align="right", format="money"),
                             Column("discount", "Skidka", align="right", format="money")],
                    rows=[{"day": str(r.day), "count": r.count,
                           "revenue": float(r.revenue), "discount": float(r.discount)} for r in rows]))
    return rpt
