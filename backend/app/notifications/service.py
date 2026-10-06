import logging
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.user import User
from app.notifications.models import Notification, NotificationDelivery
from app.notifications.templates import get_template, render_notification
from app.notifications.throttle import make_dedup_key, is_throttled, acquire_dedup_lock
from app.notifications.preferences import get_enabled_channels
from app.core.tx import transaction

logger = logging.getLogger(__name__)


def emit(db, tenant_id, trigger_key, payload, entity_type=None,
         entity_id=None, user_ids=None, locale="tj"):
    tpl = get_template(trigger_key)
    if tpl is None:
        logger.warning("Unknown trigger: %s", trigger_key)
        return None
    dk = make_dedup_key(trigger_key, entity_type, entity_id)
    if user_ids is None:
        user_ids = [r[0] for r in db.query(User.id).filter(
            User.tenant_id == tenant_id,
            User.role.in_(["OWNER", "ADMIN"])).all()]
    if not user_ids:
        return None
    title, body = render_notification(trigger_key, payload, locale)
    with transaction(db):
        acquire_dedup_lock(db, tenant_id, dk)
        if is_throttled(db, tenant_id, dk, trigger_key):
            return None
        n = Notification(tenant_id=tenant_id, user_id=None,
                         trigger_key=trigger_key, severity=tpl["severity"],
                         title_tj=title, body_tj=body,
                         entity_type=entity_type, entity_id=entity_id,
                         payload=payload, dedup_key=dk)
        db.add(n); db.flush()
        for uid in user_ids:
            user = db.query(User).filter(User.id == uid,
                                          User.tenant_id == tenant_id).first()
            if not user:
                continue
            chans = get_enabled_channels(db, tenant_id, uid, trigger_key)
            for ch in chans:
                if ch == "in_app":
                    db.add(NotificationDelivery(
                        notification_id=n.id, tenant_id=tenant_id,
                        channel="in_app", status="SENT",
                        target=f"user:{uid}", sent_at=datetime.utcnow()))
                else:
                    db.add(NotificationDelivery(
                        notification_id=n.id, tenant_id=tenant_id,
                        channel=ch, status="PENDING", target=None))
    db.refresh(n)
    return n


def emit_bulk(db, tid, events):
    c = 0
    for ev in events:
        try:
            if emit(db, tid, **ev):
                c += 1
        except Exception:
            logger.exception("emit failed: %s", ev.get("trigger_key"))
    return c
