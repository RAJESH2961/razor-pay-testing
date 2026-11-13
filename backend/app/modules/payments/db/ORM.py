import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base
from app.core.db_config import DB_SCHEMA_PAYMENT

S = DB_SCHEMA_PAYMENT


class PaymentStatus:
    PENDING = "pending"  # row exists, Razorpay order not created yet
    CREATED = "created"  # order exists, waiting for the user to pay
    PAID = "paid"
    FAILED = "failed"  # last attempt failed; a retry on the same order can still pay
    EXPIRED = "expired"  # abandoned; a checkout left open can still pay
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"
    UNDER_REVIEW = "under_review"  # Razorpay data disagrees with ours; needs a human


class PriceORM(Base):
    __tablename__ = "prices"
    __table_args__ = (
        CheckConstraint("amount_paise > 0", name="ck_prices_amount_positive"),
        CheckConstraint("duration_days > 0", name="ck_prices_duration_positive"),
        {"schema": S},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), nullable=False, unique=True)
    name = Column(String(100), nullable=False)
    # Money is always integer paise. Floats cannot represent it exactly.
    amount_paise = Column(BigInteger, nullable=False)
    currency = Column(String(3), nullable=False, server_default="INR")
    duration_days = Column(Integer, nullable=False)
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PaymentORM(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount_paise > 0", name="ck_payments_amount_positive"),
        CheckConstraint(
            "amount_refunded_paise >= 0 AND amount_refunded_paise <= amount_paise",
            name="ck_payments_refund_range",
        ),
        CheckConstraint(
            "status IN ('pending','created','paid','failed','expired','refunded',"
            "'partially_refunded','under_review')",
            name="ck_payments_status",
        ),
        # The database itself guarantees one open checkout per user and plan,
        # so a double click cannot create two orders.
        Index(
            "uq_payments_one_open_checkout",
            "user_id",
            "price_id",
            unique=True,
            postgresql_where=text("status IN ('pending','created')"),
        ),
        Index("ix_payments_user_created", "user_id", "created_at"),
        Index("ix_payments_status_created", "status", "created_at"),
        {"schema": S},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey(f"{S}.users.id"), nullable=False)
    price_id = Column(UUID(as_uuid=True), ForeignKey(f"{S}.prices.id"), nullable=False)
    # Snapshot of the charge: later price edits must not change history.
    amount_paise = Column(BigInteger, nullable=False)
    amount_refunded_paise = Column(BigInteger, nullable=False, server_default="0")
    currency = Column(String(3), nullable=False)
    duration_days = Column(Integer, nullable=False)
    status = Column(String(30), nullable=False, default=PaymentStatus.PENDING)
    razorpay_order_id = Column(String(64), unique=True)
    razorpay_payment_id = Column(String(64), unique=True)
    method = Column(String(30))
    failure_code = Column(String(100))
    failure_reason = Column(Text)
    paid_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EntitlementORM(Base):
    """What the user actually bought. Kept apart from payments so course access
    can be checked without knowing anything about payment states."""

    __tablename__ = "entitlements"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_entitlements_period"),
        CheckConstraint("status IN ('active','revoked')", name="ck_entitlements_status"),
        Index("ix_entitlements_user_ends", "user_id", "ends_at"),
        {"schema": S},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey(f"{S}.users.id"), nullable=False)
    price_id = Column(UUID(as_uuid=True), ForeignKey(f"{S}.prices.id"), nullable=False)
    # Unique: one payment can never grant access twice.
    payment_id = Column(UUID(as_uuid=True), ForeignKey(f"{S}.payments.id"), nullable=False, unique=True)
    starts_at = Column(DateTime(timezone=True), nullable=False)
    ends_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(20), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class WebhookEventORM(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('received','processed','ignored','failed')",
            name="ck_webhook_events_status",
        ),
        {"schema": S},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(String(128), nullable=False, unique=True)
    event_type = Column(String(100), nullable=False)
    payload = Column(JSONB, nullable=False)
    status = Column(String(20), nullable=False, default="received")
    error = Column(Text)
    received_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    processed_at = Column(DateTime(timezone=True))
