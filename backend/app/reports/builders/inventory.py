from datetime import datetime
from app.models.tenant import Tenant
from app.reports.types import Report, Section, Column
from app.intelligence.costing import products_valuation, stock_valuation, CostingMethod


def build_inventory(db, tenant_id, since, until, branch_id=None):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    cur = tenant.currency if tenant else "TJS"
    products = products_valuation(db, tenant_id, branch_id=branch_id,
                                  method=CostingMethod.MOVING_AVERAGE)
    total = stock_valuation(db, tenant_id, branch_id=branch_id,
                            method=CostingMethod.MOVING_AVERAGE)
    rpt = Report(key="inventory", title_tj="Ombor zaxirasi",
                 subtitle_tj=tenant.name if tenant else None,
                 meta={"currency": cur, "generated_at": datetime.utcnow().isoformat()})
    rpt.add(Section(title_tj="Umumiy",
                    columns=[Column("item", "Korsatkich"),
                             Column("value", "Qiymat", align="right")],
                    rows=[{"item": "Mahsulot turi", "value": f"{len(products):,}"},
                          {"item": "Umumiy miqdor", "value": f"{total['total_qty']:,.2f}"},
                          {"item": "Umumiy qiymat",
                           "value": f"{total['total_value']:,.2f} {cur}"}]))
    rpt.add(Section(title_tj="Mahsulotlar",
                    columns=[Column("sku", "SKU"), Column("name", "Mahsulot"),
                             Column("quantity", "Miqdor", align="right", format="float"),
                             Column("unit_cost", "Tan narx", align="right", format="money"),
                             Column("total_value", "Qiymat", align="right", format="money")],
                    rows=products))
    return rpt
