from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token, get_current_user, verify_password
from app.modules.payments.db.crud import EntitlementCRUD
from app.modules.users.db.ORM import UserORM

router = APIRouter(tags=["users"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    id: str
    email: str
    full_name: str
    phone: Optional[str]
    access_until: Optional[datetime]


@router.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(UserORM).filter(UserORM.email == body.email.lower()).first()
    password_ok = verify_password(body.password, user.password_hash if user else None)
    if user is None or not password_ok or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=MeResponse)
def me(user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    return MeResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        access_until=EntitlementCRUD.access_until(db, user.id, datetime.now(timezone.utc)),
    )
