from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.models.company import Branch
from app.models.product import Product
from app.models.supplier import Supplier
from app.models.customer import Customer


def ensure_branch(db: Session, tenant_id: int, branch_id: int) -> Branch:
    b = db.query(Branch).filter(Branch.id == branch_id,
                                 Branch.tenant_id == tenant_id).first()
    if not b:
        raise HTTPException(404, f"Filial topilmadi: {branch_id}")
    return b


def ensure_product(db: Session, tenant_id: int, product_id: int) -> Product:
    p = db.query(Product).filter(Product.id == product_id,
                                  Product.tenant_id == tenant_id).first()
    if not p:
        raise HTTPException(404, f"Mahsulot topilmadi: {product_id}")
    return p


def ensure_supplier(db: Session, tenant_id: int, supplier_id: int) -> Supplier:
    s = db.query(Supplier).filter(Supplier.id == supplier_id,
                                   Supplier.tenant_id == tenant_id).first()
    if not s:
        raise HTTPException(404, f"Supplier topilmadi: {supplier_id}")
    return s


def ensure_customer(db: Session, tenant_id: int, customer_id: int) -> Customer:
    c = db.query(Customer).filter(Customer.id == customer_id,
                                   Customer.tenant_id == tenant_id).first()
    if not c:
        raise HTTPException(404, f"Mijoz topilmadi: {customer_id}")
    return c
