PERMISSIONS = {
    "OWNER": {"*": ["*"]},
    "ADMIN": {"*": ["*"]},
    "MANAGER": {
        "sales": ["view", "create", "edit", "approve", "export"],
        "inventory": ["view", "create", "edit", "adjust", "export"],
        "purchases": ["view", "create", "edit", "approve"],
        "products": ["view", "create", "edit"],
        "customers": ["view", "create", "edit"],
        "suppliers": ["view", "create", "edit"],
        "finance": ["view", "export"],
        "reports": ["view", "export"],
        "intelligence": ["view"],
        "ai": ["use"],
    },
    "CASHIER": {
        "sales": ["view", "create"],
        "customers": ["view", "create"],
        "products": ["view"],
        "inventory": ["view"],
        "reports": ["view"],
    },
    "WAREHOUSE": {
        "inventory": ["view", "create", "edit", "adjust", "transfer", "count"],
        "products": ["view", "create", "edit"],
        "purchases": ["view", "create"],
        "suppliers": ["view"],
    },
    "ACCOUNTANT": {
        "finance": ["view", "create", "edit", "export"],
        "sales": ["view", "export"],
        "purchases": ["view", "export"],
        "customers": ["view", "edit"],
        "suppliers": ["view", "edit"],
        "reports": ["view", "export"],
        "intelligence": ["view"],
    },
    "ANALYST": {
        "sales": ["view", "export"],
        "inventory": ["view", "export"],
        "finance": ["view", "export"],
        "reports": ["view", "export"],
        "intelligence": ["view"],
        "ai": ["use"],
    },
}


def can(role: str, resource: str, action: str) -> bool:
    if role not in PERMISSIONS:
        return False
    rules = PERMISSIONS[role]
    if "*" in rules and "*" in rules["*"]:
        return True
    actions = rules.get(resource, [])
    return "*" in actions or action in actions
