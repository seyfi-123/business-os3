from fastapi import Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.core.permissions import can
from app.models.user import User
from app.models.audit import AuditLog


def current_user(authorization: str = Header(None),
                 db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Avtorizatsiya talab qilinadi")
    payload = decode_token(authorization.split(" ")[1])
    if not payload:
        raise HTTPException(401, "Token yaroqsiz")
    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if not user:
        raise HTTPException(401, "Foydalanuvchi topilmadi")
    return user


def require(resource: str, action: str):
    def checker(user: User = Depends(current_user)):
        if not can(user.role, resource, action):
            raise HTTPException(403, f"Ruxsat yoq: {resource}.{action}")
        return user
    return checker


def audit(db: Session, tenant_id: int, user_id, action: str,
          entity: str, entity_id=None, payload=None, ip=None):
    db.add(AuditLog(tenant_id=tenant_id, user_id=user_id, action=action,
                    entity=entity, entity_id=entity_id, payload=payload, ip=ip))
