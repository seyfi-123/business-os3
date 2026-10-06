from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models.company import Branch
from app.billing.models import Subscription, Invoice
from app.notifications.service import emit_bulk


def scan_billing(db, tid):
    events = []
    now = datetime.utcnow()
    for s in db.query(Subscription).filter(
            Subscription.tenant_id == tid, Subscription.status == "TRIAL",
            Subscription.trial_end.isnot(None)).all():
        if not s.trial_end:
            continue
        d = (s.trial_end - now).days
        if 0 <= d <= 3:
            events.append({"trigger_key": "billing.trial_ending",
                           "payload": {"days": d, "plan_code": s.plan_code},
                           "entity_type": "Subscription", "entity_id": s.id})
    for s in db.query(Subscription).filter(
            Subscription.tenant_id == tid, Subscription.status == "ACTIVE").all():
        if not s.current_period_end:
            continue
        d = (s.current_period_end - now).days
        if 0 <= d <= 3:
            events.append({"trigger_key": "billing.subscription_expiry",
                           "payload": {"days": d, "plan_code": s.plan_code},
                           "entity_type": "Subscription", "entity_id": s.id})
    for inv in db.query(Invoice).filter(
            Invoice.tenant_id == tid, Invoice.status.in_(["SENT", "OVERDUE"]),
            Invoice.due_at.isnot(None), Invoice.due_at < now).all():
        if not inv.due_at:
            continue
        events.append({"trigger_key": "billing.invoice_overdue",
                       "payload": {"number": inv.number,
                                   "amount": f"{inv.amount:,.0f}"},
                       "entity_type": "Invoice", "entity_id": inv.id})
    return events


def scan_forecast(db, tid, bid):
    from app.intelligence.forecast import stockout_risk, ForecastConfig
    events = []
    cfg = ForecastConfig()
    for r in stockout_risk(db, tid, bid, None, cfg):
        if r["risk"] != "critical" or (r.get("days_until_stockout") or 999) > 3:
            continue
        events.append({"trigger_key": "forecast.stockout_critical",
                       "payload": {"name": r["name"],
                                   "days": r.get("days_until_stockout"),
                                   "stock": r.get("current_stock"),
                                   "velocity": r.get("velocity")},
                       "entity_type": "Product", "entity_id": r["product_id"]})
    return events


def scan_variance(db, tid, days=7):
    from app.intelligence.variance import analyze, VarianceConfig
    events = []
    now = datetime.utcnow()
    since = now - timedelta(days=days)
    r = analyze(db, tid, since, now, branch_id=None, cfg=VarianceConfig())
    for it in r["items"]:
        if it["severity"] != "critical":
            continue
        events.append({"trigger_key": "variance.high_loss",
                       "payload": {"name": it["name"],
                                   "branch_name": it.get("branch_name") or "-",
                                   "qty": it["variance_qty"],
                                   "value": f"{abs(it['variance_value']):,.0f}"},
                       "entity_type": "InventoryCount", "entity_id": it["count_id"]})
    for x in r["recurring_offenders"]:
        events.append({"trigger_key": "variance.recurring",
                       "payload": {"name": x["name"],
                                   "branch_name": x.get("branch_name") or "-",
                                   "events": x["events"],
                                   "value": f"{x['total_lost_value']:,.0f}"},
                       "entity_type": "Product", "entity_id": x["product_id"]})
    return events


def scan_supplier(db, tid):
    from app.intelligence.supplier_anomaly import analyze, AnomalyConfig
    events = []
    r = analyze(db, tid, AnomalyConfig())
    for p in r["products"]:
        for f in p["findings"]:
            if f["kind"] == "upward_trend":
                events.append({"trigger_key": "supplier_anomaly.upward_trend",
                               "payload": {"name": p["name"],
                                           "first": f["first_price"],
                                           "last": f["last_price"],
                                           "pct": f["pct_change"]},
                               "entity_type": "Product",
                               "entity_id": p["product_id"]})
            elif f["kind"] == "supplier_gap" and f["impact"] >= 500:
                events.append({"trigger_key": "supplier_anomaly.supplier_gap",
                               "payload": {"name": p["name"],
                                           "supplier_name": f"#{f['supplier_id']}",
                                           "pct": f["pct_vs_market"],
                                           "impact": f"{f['impact']:,.0f}"},
                               "entity_type": "Product",
                               "entity_id": p["product_id"]})
    return events


def scan_all(db, tid, branch_ids=None):
    if branch_ids is None:
        branch_ids = [b.id for b in db.query(Branch).filter(
            Branch.tenant_id == tid).all()]
    all_events = []
    all_events += scan_billing(db, tid)
    for bid in branch_ids:
        all_events += scan_forecast(db, tid, bid)
    all_events += scan_variance(db, tid)
    all_events += scan_supplier(db, tid)
    emitted = emit_bulk(db, tid, all_events)
    return {"scanned_events": len(all_events),
            "emitted_notifications": emitted,
            "throttled": len(all_events) - emitted}
