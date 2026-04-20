from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..core.database import get_db
from ..models.feedback import UserFeedback
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/feedback", tags=["feedback"])

class FeedbackCreate(BaseModel):
    category: str
    message: str
    user_id: Optional[int] = None

@router.post("/")
async def create_feedback(payload: FeedbackCreate, db: Session = Depends(get_db)):
    feedback = UserFeedback(
        category=payload.category,
        message=payload.message,
        user_id=payload.user_id
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return {"status": "success", "id": feedback.id}
