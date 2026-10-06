from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require
from app.models.user import User

router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])


@router.get("/profit")
def profit(user: User = Depends(require("intelligence", "view")),
           db: Session = Depends(get_db)):
    try:
        from app.intelligence.profit import real_profit
        return real_profit(db, user.tenant_id)
    except ImportError:
        return {"available": False, "reason": "Module not installed yet"}


@router.get("/leakage")
def leakage(user: User = Depends(require("intelligence", "view")),
            db: Session = Depends(get_db)):
    try:
        from app.intelligence.leakage import detect_leakage
        return detect_leakage(db, user.tenant_id)
    except ImportError:
        return {"total_leak": 0, "findings": []}


@router.get("/dead-stock")
def dead(user: User = Depends(require("intelligence", "view")),
         db: Session = Depends(get_db)):
    try:
        from app.intelligence.dead_stock_tiers import summary as dst_summary
        return dst_summary(db, user.tenant_id)
    except ImportError:
        return {"total_capital_locked": 0, "by_tier": {}}


@router.get("/forecast")
def forecast(user: User = Depends(require("intelligence", "view")),
             db: Session = Depends(get_db)):
    try:
        from app.intelligence.forecast import revenue_forecast
        return revenue_forecast(db, user.tenant_id)
    except ImportError:
        return {"available": False, "reason": "Module not installed yet"}


@router.get("/recommendations")
def recs(user: User = Depends(require("intelligence", "view")),
         db: Session = Depends(get_db)):
    try:
        from app.intelligence.optimization import generate as opt_generate
        return opt_generate(db, user.tenant_id)
    except ImportError:
        return {"summary": {"total_actions": 0}, "actions": []}
