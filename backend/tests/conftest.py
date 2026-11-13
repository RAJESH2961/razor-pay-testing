"""Runs against a real throwaway Postgres: the guarantees under test (locks, partial
unique indexes, ON CONFLICT) do not exist on SQLite."""
import hashlib
import hmac
import os
import tempfile
import uuid

import pgserver
import pytest
from sqlalchemy.engine import make_url

_server = pgserver.get_server(tempfile.mkdtemp(), cleanup_mode="stop")
_url = make_url(_server.get_uri())

os.environ.update(
    PAYMENT_TESTING="1",
    APP_ENV="test",
    PG_USER=_url.username or "postgres",
    PG_PASS="",
    PG_HOST=_url.host or _url.query.get("host", ""),
    PG_PORT=str(_url.port or 5432),
    DB_NAME=_url.database,
    DB_SCHEMA="payment_pytest",
    PG_SSLMODE="disable",
    RAZORPAY_KEY_ID="rzp_test_key",
    RAZORPAY_KEY_SECRET="test-key-secret",
    RAZORPAY_WEBHOOK_SECRET="test-webhook-secret",
    JWT_SECRET="x" * 40,
)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.payments.db.ORM import PriceORM  # noqa: E402
from app.modules.payments.gateway import GatewayError, get_gateway  # noqa: E402
from app.modules.users.db.ORM import UserORM  # noqa: E402


class FakeGateway:
    """Real signature logic, fake network."""

    def __init__(self):
        self.orders = []
        self.payments = {}
        self.fail_create = False
        self.fail_fetch = False

    def create_order(self, amount_paise, currency, receipt, notes):
        if self.fail_create:
            raise GatewayError("down")
        order_id = f"order_{uuid.uuid4().hex[:14]}"
        self.orders.append(order_id)
        return {"id": order_id}

    def fetch_payment(self, payment_id):
        if self.fail_fetch:
            raise GatewayError("down")
        return self.payments[payment_id]

    def capture_payment(self, payment_id, amount_paise, currency):
        self.payments[payment_id]["status"] = "captured"
        return self.payments[payment_id]

    def order_payments(self, order_id):
        return [p for p in self.payments.values() if p["order_id"] == order_id]

    def checkout_signature_valid(self, order_id, payment_id, signature):
        expected = sign(f"{order_id}|{payment_id}".encode(), "test-key-secret")
        return hmac.compare_digest(expected, signature)

    def webhook_signature_valid(self, raw_body, signature):
        return hmac.compare_digest(sign(raw_body, "test-webhook-secret"), signature)

    def add_payment(self, order_id, amount=29900, status="captured", **extra):
        payment_id = f"pay_{uuid.uuid4().hex[:14]}"
        self.payments[payment_id] = {
            "id": payment_id,
            "order_id": order_id,
            "amount": amount,
            "currency": "INR",
            "status": status,
            "method": "upi",
            **extra,
        }
        return self.payments[payment_id]


def sign(message: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


@pytest.fixture(scope="session", autouse=True)
def schema():
    command.upgrade(Config("alembic.ini"), "head")
    yield
    _server.cleanup()


@pytest.fixture
def gateway():
    return FakeGateway()


@pytest.fixture
def client(gateway):
    app.dependency_overrides[get_gateway] = lambda: gateway
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def price(db):
    row = PriceORM(
        code=f"monthly-{uuid.uuid4().hex[:8]}",
        name="Monthly",
        amount_paise=29900,
        currency="INR",
        duration_days=30,
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def user(db):
    row = UserORM(email=f"{uuid.uuid4().hex}@example.com", full_name="Test", password_hash="x")
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def auth(user):
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}
