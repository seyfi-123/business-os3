TEMPLATES = {
    "billing.trial_ending": {"severity": "warning",
        "title_tj": "Sinab korish muddati tugaydi",
        "body_tj": "Sinab korish muddati {days} kundan keyin tugaydi.",
        "channels": ("in_app", "email")},
    "billing.subscription_expiry": {"severity": "warning",
        "title_tj": "Obuna muddati tugaydi",
        "body_tj": "Obunangiz {days} kundan keyin tugaydi (plan: {plan_code}).",
        "channels": ("in_app", "email")},
    "billing.invoice_overdue": {"severity": "critical",
        "title_tj": "Invoice tolanmagan",
        "body_tj": "Invoice #{number} ({amount} TJS) muddati otdi.",
        "channels": ("in_app", "email")},
    "forecast.stockout_critical": {"severity": "critical",
        "title_tj": "Mahsulot tugash arafasida",
        "body_tj": "{name} - {days} kun ichida tugaydi ({stock} dona, {velocity}/kun).",
        "channels": ("in_app", "telegram")},
    "variance.high_loss": {"severity": "critical",
        "title_tj": "Inventarizatsiyada katta yoqotish",
        "body_tj": "{branch_name} - {name}: {qty} dona yoqotildi ({value} TJS).",
        "channels": ("in_app", "telegram")},
    "variance.recurring": {"severity": "warning",
        "title_tj": "Takrorlanuvchi yoqotish",
        "body_tj": "{name} - {branch_name}: {events} marta yoqotish (jami {value} TJS).",
        "channels": ("in_app",)},
    "supplier_anomaly.upward_trend": {"severity": "warning",
        "title_tj": "Supplier narxi osib bormoqda",
        "body_tj": "{name} - {first} -> {last} (+{pct}%).",
        "channels": ("in_app", "email")},
    "supplier_anomaly.supplier_gap": {"severity": "opportunity",
        "title_tj": "Supplier narxi bozordan qimmat",
        "body_tj": "{name}: {supplier_name} {pct}% qimmat.",
        "channels": ("in_app",)},
}


def get_template(k):
    return TEMPLATES.get(k)


def list_templates():
    return [{"key": k, "severity": v["severity"],
             "title_tj": v["title_tj"],
             "default_channels": list(v["channels"])} for k, v in TEMPLATES.items()]


def render_notification(k, payload, locale="tj"):
    tpl = TEMPLATES.get(k)
    if not tpl:
        return k, ""
    try:
        body = tpl["body_tj"].format(**payload)
    except (KeyError, IndexError):
        body = tpl["body_tj"]
    return tpl["title_tj"], body
