import hashlib
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.notifications.models import Notification


WINDOWS = {"billing.trial_ending": 24, "billing.subscription_expiry": 24,
           "billing.invoice_overdue": 48, "forecast.stockout_critical": 12,
           "variance.high_loss": 168, "variance.recurring": 168,
           "supplier_anomaly.upward_trend": 168, "supplier_anomaly.supplier_gap": 336}


def make_dedup_key(trigger_key, entity_type, entity_id):
    return f"{trigger_key}:{entity_type or '-'}:{entity_id or 0}"


def _lk(tid, dk):
    h = hashlib.blake2b(dk.encode(), digest_size=8).digest()
    return (tid << 32) ^ (int.from_bytes(h, "big") & 0x7FFFFFFFFFFFFFFF)


def acquire_dedup_lock(db, tid, dk):
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _lk(tid, dk)})


def is_throttled(db, tid, dk, trigger_key):
    since = datetime.utcnow() - timedelta(hours=WINDOWS.get(trigger_key, 24))
    return db.query(Notification.id).filter(
        Notification.tenant_id == tid, Notification.dedup_key == dk,
        Notification.created_at >= since).first() is not None
