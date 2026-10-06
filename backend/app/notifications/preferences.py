from sqlalchemy.orm import Session
from app.notifications.models import NotificationPreference
from app.notifications.templates import get_template


def get_enabled_channels(db, tid, uid, tk):
    rows = (db.query(NotificationPreference).filter(
        NotificationPreference.tenant_id == tid,
        NotificationPreference.user_id == uid,
        NotificationPreference.trigger_key.in_([tk, "*"])).all())
    if not rows:
        tpl = get_template(tk)
        return list(tpl["channels"]) if tpl else ["in_app"]
    spec = [r for r in rows if r.trigger_key == tk]
    wild = [r for r in rows if r.trigger_key == "*"]
    src = spec or wild
    enabled = [r.channel for r in src if r.enabled]
    return enabled or ["in_app"]


def set_preference(db, tid, uid, tk, ch, enabled):
    row = (db.query(NotificationPreference).filter(
        NotificationPreference.tenant_id == tid,
        NotificationPreference.user_id == uid,
        NotificationPreference.trigger_key == tk,
        NotificationPreference.channel == ch).first())
    if row:
        row.enabled = enabled
    else:
        row = NotificationPreference(tenant_id=tid, user_id=uid,
                                      trigger_key=tk, channel=ch,
                                      enabled=enabled)
        db.add(row)
    db.commit(); db.refresh(row)
    return row


def list_preferences(db, tid, uid):
    rows = (db.query(NotificationPreference).filter(
        NotificationPreference.tenant_id == tid,
        NotificationPreference.user_id == uid).all())
    return [{"trigger_key": r.trigger_key, "channel": r.channel,
             "enabled": r.enabled} for r in rows]
