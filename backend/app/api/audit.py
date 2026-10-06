from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require
from app.models.user import User
from app.models.audit import AuditLog

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
def list_logs(limit: int = 100, action: str | None = None,
              entity: str | None = None,
              user: User = Depends(require("reports", "view")),
              db: Session = Depends(get_db)):
    q = db.query(AuditLog).filter(AuditLog.tenant_id == user.tenant_id)
    if action:
        q = q.filter(AuditLog.action == action)
    if entity:
        q = q.filter(AuditLog.entity == entity)
    rows = q.order_by(AuditLog.created_at.desc()).limit(limit).all()
    return [{"id": a.id, "user_id": a.user_id, "action": a.action,
             "entity": a.entity, "entity_id": a.entity_id,
             "payload": a.payload,
             "created_at": a.created_at} for a in rows]
