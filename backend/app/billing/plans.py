from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Plan:
    code: str
    name_tj: str
    description_tj: str
    price_tjs: float
    billing_period_days: int
    trial_days: int
    limits: dict
    features: dict
    def to_dict(self):
        return asdict(self)


PLANS = {
    "STARTER": Plan("STARTER", "Стартер", "Yangi boshlagan biznes uchun", 0, 30, 14,
                    {"branches": 1, "employees": 3, "products": 200,
                     "invoices_per_month": 500, "purchases_per_month": 100},
                    {"intelligence": False, "ai_copilot": False, "api_access": False,
                     "multi_branch": False, "supplier_anomaly": False,
                     "variance": False, "forecast": False, "optimization": False}),
    "BUSINESS": Plan("BUSINESS", "Бизнес", "Osib borayotgan biznes uchun", 199, 30, 14,
                     {"branches": 5, "employees": 25, "products": 5000,
                      "invoices_per_month": 10000, "purchases_per_month": 2000},
                     {"intelligence": True, "ai_copilot": False, "api_access": False,
                      "multi_branch": True, "supplier_anomaly": True,
                      "variance": True, "forecast": True, "optimization": False}),
    "ENTERPRISE": Plan("ENTERPRISE", "Корхона", "Yirik korxonalar uchun", 499, 30, 14,
                       {"branches": None, "employees": None, "products": None,
                        "invoices_per_month": None, "purchases_per_month": None},
                       {"intelligence": True, "ai_copilot": True, "api_access": True,
                        "multi_branch": True, "supplier_anomaly": True,
                        "variance": True, "forecast": True, "optimization": True}),
}
DEFAULT_PLAN = "STARTER"


def get_plan(code):
    if code not in PLANS:
        raise ValueError(f"Nomahlum plan: {code}")
    return PLANS[code]


def list_plans():
    return [p.to_dict() for p in PLANS.values()]
