from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Required values have no default, so a bad deploy fails at startup."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "development"

    RAZORPAY_KEY_ID: str
    RAZORPAY_KEY_SECRET: str
    RAZORPAY_WEBHOOK_SECRET: str
    RAZORPAY_TIMEOUT_SECONDS: float = 4.0

    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60

    CORS_ORIGINS: str = "http://localhost:5173"

    # An unpaid order is reused for this long, then a fresh one is created.
    ORDER_REUSE_MINUTES: int = 15
    # A "pending" row older than this means the process died mid-create.
    PENDING_STALE_SECONDS: int = 120

    @field_validator("JWT_SECRET")
    @classmethod
    def jwt_secret_is_strong(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return value

    @property
    def cors_origin_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


settings = AppSettings()

if settings.APP_ENV == "production" and settings.RAZORPAY_KEY_ID.startswith("rzp_test_"):
    raise RuntimeError("Refusing to start production with a Razorpay test key")
