import logging
from app.core.database import SessionLocal
from app.models.tenant import Tenant
from app.notifications.triggers import scan_all
from app.notifications.dispatcher import dispatch_pending

logger = logging.getLogger(__name__)


def run_once():
    db = SessionLocal()
    total = 0
    tenants_count = 0
    try:
        tenants = db.query(Tenant).filter(Tenant.active.is_(True)).all()
        tenants_count = len(tenants)
        for t in tenants:
            try:
                r = scan_all(db, t.id)
                total += r["emitted_notifications"]
            except Exception:
                logger.exception("Tenant %s scan failed", t.id)
    finally:
        db.close()
    disp = dispatch_pending()
    return {"tenants": tenants_count, "emitted": total, **disp}
