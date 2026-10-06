from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.database import get_db
from app.core.deps import current_user, require
from app.models.user import User
from app.notifications.models import Notification, NotificationDelivery
from app.notifications.templates import list_templates
from app.notifications.preferences import set_preference, list_preferences
from app.notifications.triggers import scan_all
from app.notifications.dispatcher import dispatch_pending

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("/templates")
def templates():
    return list_templates()


@router.get("")
def list_notifs(limit: int = 50, unread_only: bool = False,
                user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    q = db.query(Notification).filter(Notification.tenant_id == user.tenant_id)
    if unread_only:
        q = q.filter(Notification.read_at.is_(None))
    rows = q.order_by(Notification.created_at.desc()).limit(limit).all()
    return [_d(n, db) for n in rows]


@router.get("/unread-count")
def unread(user: User = Depends(current_user), db: Session = Depends(get_db)):
    n = (db.query(func.count(Notification.id)).filter(
        Notification.tenant_id == user.tenant_id,
        Notification.read_at.is_(None)).scalar() or 0)
    return {"count": int(n)}


@router.post("/{notification_id}/read")
def mark_read(notification_id: int, user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    n = db.query(Notification).filter(Notification.id == notification_id,
                                       Notification.tenant_id == user.tenant_id).first()
    if not n:
        raise HTTPException(404, "Topilmadi")
    if n.read_at is None:
        n.read_at = datetime.utcnow()
        db.commit()
    return {"id": n.id, "read_at": n.read_at}


@router.post("/read-all")
def read_all(user: User = Depends(current_user), db: Session = Depends(get_db)):
    now = datetime.utcnow()
    n = db.query(Notification).filter(Notification.tenant_id == user.tenant_id,
                                       Notification.read_at.is_(None))        .update({"read_at": now}, synchronize_session=False)
    db.commit()
    return {"marked": int(n)}


class PrefIn(BaseModel):
    trigger_key: str = Field(min_length=1, max_length=64)
    channel: str = Field(pattern="^(in_app|telegram|email)$")
    enabled: bool


@router.get("/preferences")
def get_prefs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return list_preferences(db, user.tenant_id, user.id)


@router.post("/preferences")
def set_pref(data: PrefIn, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    row = set_preference(db, user.tenant_id, user.id,
                         data.trigger_key, data.channel, data.enabled)
    return {"trigger_key": row.trigger_key, "channel": row.channel,
            "enabled": row.enabled}


@router.post("/scan")
def scan(user: User = Depends(require("intelligence", "view")),
         db: Session = Depends(get_db)):
    if user.role not in ("OWNER", "ADMIN"):
        raise HTTPException(403, "Faqat OWNER/ADMIN")
    return scan_all(db, user.tenant_id)


@router.post("/dispatch")
def dispatch(user: User = Depends(require("intelligence", "view")),
             db: Session = Depends(get_db)):
    if user.role not in ("OWNER", "ADMIN"):
        raise HTTPException(403, "Faqat OWNER/ADMIN")
    return dispatch_pending()


def _d(n, db):
    ds = db.query(NotificationDelivery).filter(
        NotificationDelivery.notification_id == n.id).all()
    return {"id": n.id, "trigger_key": n.trigger_key, "severity": n.severity,
            "title_tj": n.title_tj, "body_tj": n.body_tj,
            "entity_type": n.entity_type, "entity_id": n.entity_id,
            "payload": n.payload, "created_at": n.created_at,
            "read_at": n.read_at,
            "deliveries": [{"channel": d.channel, "status": d.status,
                            "target": d.target, "error": d.error} for d in ds]}
