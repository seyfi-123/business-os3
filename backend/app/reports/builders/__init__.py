from app.reports.builders.pnl import build_pnl
from app.reports.builders.sales import build_sales
from app.reports.builders.inventory import build_inventory
from app.reports.builders.dead_stock import build_dead_stock
from app.reports.builders.supplier import build_supplier
from app.reports.builders.variance import build_variance
from app.reports.builders.forecast import build_forecast
from app.reports.builders.billing import build_billing

BUILDERS = {"pnl": build_pnl, "sales": build_sales, "inventory": build_inventory,
            "dead_stock": build_dead_stock, "supplier": build_supplier,
            "variance": build_variance, "forecast": build_forecast,
            "billing": build_billing}
