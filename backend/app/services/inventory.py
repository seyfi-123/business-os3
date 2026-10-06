from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.product import Inventory


def get_or_create_inventory(db: Session, tenant_id: int,
                            branch_id: int, product_id: int,
                            lock: bool = False) -> Inventory:
    q = db.query(Inventory).filter(
        Inventory.tenant_id == tenant_id,
        Inventory.branch_id == branch_id,
        Inventory.product_id == product_id,
    )
    if lock:
        q = q.with_for_update()
    inv = q.first()
    if inv:
        return inv

    stmt = (
        pg_insert(Inventory)
        .values(tenant_id=tenant_id, branch_id=branch_id,
                product_id=product_id, quantity=0)
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "branch_id", "product_id"]
        )
    )
    db.execute(stmt)
    db.flush()

    q2 = db.query(Inventory).filter(
        Inventory.tenant_id == tenant_id,
        Inventory.branch_id == branch_id,
        Inventory.product_id == product_id,
    )
    if lock:
        q2 = q2.with_for_update()
    return q2.one()
