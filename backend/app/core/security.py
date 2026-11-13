from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.modules.users.db.ORM import UserORM

bearer = HTTPBearer(auto_error=False)

# Compared against when the email is unknown, so response time does not reveal
# which emails are registered.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt()).decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: Optional[str]) -> bool:
    # bcrypt only reads 72 bytes and newer versions reject longer input.
    return bcrypt.checkpw(password.encode()[:72], (password_hash or _DUMMY_HASH).encode())


def create_access_token(user_id: UUID) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(claims, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: Session = Depends(get_db),
) -> UserORM:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if credentials is None:
        raise unauthorized
    try:
        claims = jwt.decode(
            credentials.credentials, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        user_id = UUID(claims["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized

    user = db.get(UserORM, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    return user
