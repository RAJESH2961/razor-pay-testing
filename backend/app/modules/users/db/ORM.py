import uuid

from sqlalchemy import Boolean, Column, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base
from app.core.db_config import DB_SCHEMA_PAYMENT


class UserORM(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": DB_SCHEMA_PAYMENT}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), nullable=False, unique=True)
    full_name = Column(String(255), nullable=False)
    phone = Column(String(20))
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
