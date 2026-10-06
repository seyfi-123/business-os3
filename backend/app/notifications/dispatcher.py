import logging
from datetime import datetime
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.core.tx import transaction
from app.models.user import User
from app.notifications.models import Notification, NotificationDelivery
from app.notifications.channels import CHANNELS

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
BATCH = 100


def dispatch_pending(db=None):
    own = db is None
    if own:
        db = SessionLocal()
    sent = failed = skipped = 0
    try:
        rows = (db.query(NotificationDelivery)
                .filter(NotificationDelivery.status == "PENDING",
                        NotificationDelivery.attempts < MAX_ATTEMPTS)
                .order_by(NotificationDelivery.attempted_at.asc())
                .limit(BATCH).all())
        if not rows:
            return {"sent": 0, "failed": 0, "skipped": 0}
        jobs = []
        for d in rows:
            n = db.query(Notification).filter(Notification.id == d.notification_id).first()
            if not n:
                continue
            u = (db.query(User).filter(User.tenant_id == d.tenant_id,
                                        User.role.in_(["OWNER", "ADMIN"]))
                 .order_by(User.id.asc()).first())
            jobs.append({"delivery_id": d.id, "channel": d.channel,
                         "notification": n, "user": u,
                         "attempts": d.attempts or 1})
        db.expunge_all()
        db.commit()
        results = []
        for j in jobs:
            ch = CHANNELS.get(j["channel"])
            if ch is None or not ch.is_available():
                results.append({"delivery_id": j["delivery_id"],
                                "status": "SKIPPED", "target": None,
                                "error": f"channel {j['channel']} unavailable",
                                "attempts": j["attempts"]})
                continue
            tgt = ch.resolve_target(j["user"])
            if not tgt:
                results.append({"delivery_id": j["delivery_id"],
                                "status": "SKIPPED", "target": None,
                                "error": "no target", "attempts": j["attempts"]})
                continue
            try:
                r = ch.send(j["notification"], j["user"], tgt)
                results.append({"delivery_id": j["delivery_id"],
                                "status": r.status, "target": r.target,
                                "error": r.error, "attempts": j["attempts"]})
            except Exception as e:
                logger.exception("dispatch send failed")
                results.append({"delivery_id": j["delivery_id"],
                                "status": "FAILED", "target": tgt,
                                "error": str(e)[:200], "attempts": j["attempts"]})
        with transaction(db):
            for r in results:
                d = db.query(NotificationDelivery).filter(
                    NotificationDelivery.id == r["delivery_id"]).first()
                if not d:
                    continue
                d.status = r["status"]
                d.target = r["target"]
                d.error = r["error"]
                d.attempts = (r["attempts"] or 1) + (1 if r["status"] == "FAILED" else 0)
                d.attempted_at = datetime.utcnow()
                if r["status"] == "SENT":
                    d.sent_at = datetime.utcnow()
                    sent += 1
                elif r["status"] == "SKIPPED":
                    skipped += 1
                else:
                    failed += 1
        return {"sent": sent, "failed": failed, "skipped": skipped}
    finally:
        if own:
            db.close()


def retry_failed(db=None):
    own = db is None
    if own:
        db = SessionLocal()
    try:
        n = (db.query(NotificationDelivery)
             .filter(NotificationDelivery.status == "FAILED",
                     NotificationDelivery.attempts < MAX_ATTEMPTS)
             .update({"status": "PENDING"}, synchronize_session=False))
        db.commit()
        return {"requeued": int(n)}
    finally:
        if own:
            db.close()
