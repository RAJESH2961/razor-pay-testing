import os
from typing import Dict, Union

from dotenv import load_dotenv
from pydantic_settings import BaseSettings
from sqlalchemy.engine import URL

# Tests set PAYMENT_TESTING so a developer's .env can never point them at real data.
load_dotenv(override=os.getenv("PAYMENT_TESTING") != "1")

DB_SCHEMA_PAYMENT = os.getenv("DB_SCHEMA", "payment_test")


class DBSettings(BaseSettings):
    """Database configuration settings"""

    model_config = {"extra": "ignore"}

    DB_USERNAME: str = os.getenv("PG_USER", "webvoid_dev")
    DB_PASSWORD: str = os.getenv("PG_PASS", "")
    DB_HOST: str = os.getenv("PG_HOST", "localhost")
    DB_PORT: int = int(os.getenv("PG_PORT", "5432"))
    DB_DATABASE: str = os.getenv("DB_NAME", "postgres")
    DB_SSLMODE: str = os.getenv("PG_SSLMODE", "require")

    SQLALCHEMY_PRINT_SQL: bool = os.getenv("SQLALCHEMY_PRINT_SQL", "false").lower() == "true"

    POSTGRES_CONN_POOL_SIZE: int = int(os.getenv("POSTGRES_POOL_SIZE", "10"))
    POSTGRES_CONN_MAX_OVERFLOW: int = int(os.getenv("POSTGRES_MAX_OVERFLOW", "20"))
    POSTGRES_CONN_POOL_RECYCLE: int = int(os.getenv("POSTGRES_POOL_RECYCLE", "3600"))
    POSTGRES_CONN_POOL_PRE_PING: bool = True
    POSTGRES_CONN_TIMEOUT: int = int(os.getenv("POSTGRES_CONN_TIMEOUT", "15"))

    @property
    def POSTGRES_DB_URL(self) -> URL:
        """Generate PostgreSQL database URL"""
        return URL.create(
            "postgresql+psycopg2",
            username=self.DB_USERNAME,
            password=self.DB_PASSWORD,
            host=self.DB_HOST,
            port=self.DB_PORT,
            database=self.DB_DATABASE,
        )

    def get_connection_args(self, schema: str = DB_SCHEMA_PAYMENT) -> Dict[str, Union[str, int]]:
        """Get connection arguments for a specific schema"""
        return {
            "connect_timeout": self.POSTGRES_CONN_TIMEOUT,
            "sslmode": self.DB_SSLMODE,
            "options": f"-csearch_path={schema}",
        }


db_settings = DBSettings()
