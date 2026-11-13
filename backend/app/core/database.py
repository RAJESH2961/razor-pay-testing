"""
Database engine and session management for SQLAlchemy.
Tables are owned by Alembic migrations, so there is no create_all here.
"""
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.core.db_config import DB_SCHEMA_PAYMENT, db_settings

Base = declarative_base()

engine = create_engine(
    db_settings.POSTGRES_DB_URL,
    pool_size=db_settings.POSTGRES_CONN_POOL_SIZE,
    max_overflow=db_settings.POSTGRES_CONN_MAX_OVERFLOW,
    pool_recycle=db_settings.POSTGRES_CONN_POOL_RECYCLE,
    pool_pre_ping=db_settings.POSTGRES_CONN_POOL_PRE_PING,
    echo=db_settings.SQLALCHEMY_PRINT_SQL,
    connect_args=db_settings.get_connection_args(schema=DB_SCHEMA_PAYMENT),
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
