from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import hash_password, verify_password, create_token
from app.models.tenant import Tenant
from app.models.user import User
from app.models.company import Company, Branch
from app.schemas.auth import RegisterIn, LoginIn, TokenOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut)
def register(data: RegisterIn, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == data.owner_email).first():
        raise HTTPException(400, "Bu email allaqachon royxatdan otgan")

    tenant = Tenant(name=data.company_name, industry=data.business_type,
                    country=data.country, currency=data.currency)
    db.add(tenant)
    db.flush()

    company = Company(tenant_id=tenant.id, name=data.company_name,
                      currency=data.currency, country=data.country)
    db.add(company)
    db.flush()

    for i in range(max(1, data.branches_count)):
        db.add(Branch(tenant_id=tenant.id, company_id=company.id,
                      name=f"Filial {i+1}", kind="BRANCH"))

    user = User(tenant_id=tenant.id, email=data.owner_email,
                full_name=data.owner_name, role="OWNER",
                password_hash=hash_password(data.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_token({"sub": str(user.id), "tenant": tenant.id,
                          "role": user.role})
    return {"access_token": token}


@router.post("/login", response_model=TokenOut)
def login(data: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == data.email).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "Email yoki parol xato")
    token = create_token({"sub": str(user.id), "tenant": user.tenant_id,
                          "role": user.role})
    return {"access_token": token}
