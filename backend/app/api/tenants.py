from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import current_user
from app.models.user import User
from app.models.tenant import Tenant

router = APIRouter(prefix="/api/tenants", tags=["tenants"])


INDUSTRIES_TJ = [
    {"code": "retail", "label": "Dukon / Retail"},
    {"code": "restaurant", "label": "Restoran / Kafe"},
    {"code": "pharmacy", "label": "Apteka"},
    {"code": "fashion", "label": "Kiyim-kechak"},
    {"code": "auto", "label": "Avtoservis"},
    {"code": "construction", "label": "Qurilish"},
    {"code": "wholesale", "label": "Distribyutor"},
    {"code": "beauty", "label": "Salon / Beauty"},
    {"code": "manufacturing", "label": "Ishlab chiqarish"},
    {"code": "other", "label": "Boshqa"},
]


@router.get("/industries")
def industries():
    return INDUSTRIES_TJ


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    return {
        "tenant": {
            "id": tenant.id, "name": tenant.name,
            "industry": tenant.industry,
            "currency": tenant.currency, "country": tenant.country,
        },
        "user": {"id": user.id, "name": user.full_name, "role": user.role},
    }
