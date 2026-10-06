from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require
from app.models.user import User

router = APIRouter(prefix="/api/ai", tags=["ai"])


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    branch_id: int | None = None


@router.post("/copilot")
def copilot(data: AskIn,
            user: User = Depends(require("ai", "use")),
            db: Session = Depends(get_db)):
    try:
        from app.ai.copilot import ask
        resp = ask(db, user.tenant_id, data.question, data.branch_id)
        return resp.to_dict()
    except ImportError:
        return {
            "answer_tj": "AI Copilot hali ornatilmagan.",
            "mode": "local", "intent": "empty", "used_sections": [],
        }
