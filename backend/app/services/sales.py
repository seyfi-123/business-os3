from collections import defaultdict
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.models.sale import Sale, SaleItem
from app.models.product import Product, InventoryMovement
from app.models.customer import Customer
from app.models.audit import AuditLog
from app.core.validators import ensure_branch, ensure_customer
from app.core.tx import transaction
from app.services.inventory import get_or_create_inventory


def _aggregate_items(items):
    acc = defaultdict(lambda: {"quantity": 0.0, "price": None})
    for it in items:
        pid = it["product_id"]
        acc[pid]["quantity"] += float(it["quantity"])
        if it.get("price") is not None:
            acc[pid]["price"] = float(it["price"])
    return [{"product_id": pid, "quantity": v["quantity"],
             "price": v["price"]} for pid, v in acc.items()]


def create_sale(db: Session, tenant_id: int, branch_id: int,
                customer_id, items, discount: float, payment_type: str,
                user_id=None, ip=None) -> Sale:
    if not items:
        raise HTTPException(400, "Sotuv bosh bolishi mumkin emas")
    if discount < 0:
        raise HTTPException(400, "Skidka manfiy")
    if payment_type not in ("CASH", "CARD", "CREDIT"):
        raise HTTPException(400, "Tolov turi notogri")

    ensure_branch(db, tenant_id, branch_id)

    if payment_type == "CREDIT":
        if not customer_id:
            raise HTTPException(400, "Qarzga sotuv uchun mijoz shart")
        ensure_customer(db, tenant_id, customer_id)
    elif customer_id:
        ensure_customer(db, tenant_id, customer_id)

    items = _aggregate_items(items)
    for it in items:
        if it["quantity"] <= 0:
            raise HTTPException(400, "Miqdor 0 dan katta bolishi kerak")

    pid_set = {it["product_id"] for it in items}
    found = db.query(Product).filter(
        Product.tenant_id == tenant_id, Product.id.in_(pid_set)
    ).all()
    if len(found) != len(pid_set):
        raise HTTPException(404, "Bazi mahsulotlar topilmadi")
    for p in found:
        if not p.active:
            raise HTTPException(400, f"Mahsulot arxivlangan: {p.name}")

    with transaction(db):
        products_locked = {}
        for pid in sorted(pid_set):
            p = db.query(Product).filter(
                Product.id == pid, Product.tenant_id == tenant_id
            ).with_for_update().first()
            products_locked[pid] = p

        subtotal = 0.0
        prepared = []
        for it in items:
            pid = it["product_id"]
            qty = it["quantity"]
            product = products_locked[pid]
            price = float(it["price"] if it["price"] is not None
                          else product.sale_price)
            if price < 0:
                raise HTTPException(400, "Narx manfiy")
            inv = get_or_create_inventory(db, tenant_id, branch_id, pid,
                                          lock=True)
            if inv.quantity < qty:
                raise HTTPException(400,
                    f"Omborda yetarli emas: {product.name}")
            subtotal += price * qty
            prepared.append((product, inv, qty, price))

        if discount > subtotal:
            raise HTTPException(400, "Skidka jamidan katta")

        sale = Sale(
            tenant_id=tenant_id, branch_id=branch_id,
            customer_id=customer_id,
            total=round(subtotal - discount, 2),
            discount=discount, payment_type=payment_type,
        )
        db.add(sale)
        db.flush()

        for product, inv, qty, price in prepared:
            db.add(SaleItem(
                tenant_id=tenant_id, sale_id=sale.id,
                product_id=product.id, quantity=qty,
                price=price, cost=product.cost_price or 0,
            ))
            inv.quantity -= qty
            db.add(InventoryMovement(
                tenant_id=tenant_id, branch_id=branch_id,
                product_id=product.id, delta=-qty,
                reason="SALE", ref_id=sale.id, user_id=user_id,
            ))

        if payment_type == "CREDIT" and customer_id:
            c = db.query(Customer).filter(
                Customer.id == customer_id,
                Customer.tenant_id == tenant_id,
            ).first()
            c.debt = (c.debt or 0) + sale.total

        db.add(AuditLog(
            tenant_id=tenant_id, user_id=user_id, action="CREATE",
            entity="Sale", entity_id=sale.id,
            payload={"total": sale.total, "items": len(prepared)},
            ip=ip,
        ))

    db.refresh(sale)
    return sale
