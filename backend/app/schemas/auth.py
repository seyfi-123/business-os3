from pydantic import BaseModel, EmailStr


class RegisterIn(BaseModel):
    business_type: str
    company_name: str
    branches_count: int = 1
    employees_count: int = 1
    currency: str = "TJS"
    country: str = "TJ"
    owner_email: EmailStr
    owner_name: str
    password: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
