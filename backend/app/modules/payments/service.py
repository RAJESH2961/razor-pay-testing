"""
Payment rules. Three independent paths can report the same payment: the browser
(verify), the webhook and the reconcile job. They can arrive in any order, any
number of times, so all of them end in _apply_gateway_payment, which is idempotent.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.payments.db.crud import (
    OPEN_STATUSES,
    EntitlementCRUD,
    PaymentCRUD,
    PriceCRUD,
    WebhookEventCRUD,
)
from app.modules.payments.db.ORM import EntitlementORM, PaymentORM, PaymentStatus, PriceORM
from app.modules.payments.gateway import GatewayError, RazorpayGateway
from app.modules.users.db.ORM import UserORM

REFUNDED_STATUSES = (PaymentStatus.REFUNDED, PaymentStatus.PARTIALLY_REFUNDED)


class PaymentError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Checkout


def create_checkout(
    db: Session, gateway: RazorpayGateway, user: UserORM, price_code: str
) -> tuple[PaymentORM, PriceORM]:
    price = PriceCRUD.get_active_by_code(db, price_code)
    if price is None:
        raise PaymentError(404, "Plan not found")

    existing = PaymentCRUD.get_open_for_update(db, user.id, price.id)
    if existing is not None:
        age = _now() - existing.created_at
        if existing.status == PaymentStatus.PENDING:
            if age < timedelta(seconds=settings.PENDING_STALE_SECONDS):
                raise PaymentError(409, "Checkout is already being created, try again")
        elif (
            age < timedelta(minutes=settings.ORDER_REUSE_MINUTES)
            and existing.amount_paise == price.amount_paise
            and existing.currency == price.currency
        ):
            db.commit()
            return existing, price
        existing.status = PaymentStatus.EXPIRED
        # Flush now: SQLAlchemy runs inserts before updates, and the new row
        # would otherwise collide with this one on the open-checkout index.
        db.flush()

    # Commit before calling Razorpay: no row lock is held across a network call,
    # and a crash leaves a visible row rather than an order we know nothing about.
    payment = PaymentORM(
        user_id=user.id,
        price_id=price.id,
        amount_paise=price.amount_paise,
        currency=price.currency,
        duration_days=price.duration_days,
        status=PaymentStatus.PENDING,
    )
    db.add(payment)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise PaymentError(409, "Checkout is already being created, try again")

    try:
        order = gateway.create_order(
            payment.amount_paise,
            payment.currency,
            receipt=payment.id.hex,
            notes={"payment_id": str(payment.id), "plan": price.code},
        )
    except GatewayError as exc:
        payment.status = PaymentStatus.FAILED
        payment.failure_reason = "Could not create order"
        db.commit()
        raise PaymentError(502, "Payment provider is unavailable, try again") from exc

    payment.razorpay_order_id = order["id"]
    payment.status = PaymentStatus.CREATED
    db.commit()
    logger.info("Created order {} for payment {}", order["id"], payment.id)
    return payment, price


def verify_checkout(
    db: Session,
    gateway: RazorpayGateway,
    user: UserORM,
    order_id: str,
    payment_id: str,
    signature: str,
) -> PaymentORM:
    payment = PaymentCRUD.get_by_order(db, order_id)
    if payment is None or payment.user_id != user.id:
        raise PaymentError(404, "Payment not found")

    # A bad signature does not touch the row: anyone can send one, so it says
    # nothing about the real payment.
    if not gateway.checkout_signature_valid(order_id, payment_id, signature):
        logger.warning("Invalid checkout signature for order {}", order_id)
        raise PaymentError(400, "Invalid payment signature")

    try:
        entity = gateway.fetch_payment(payment_id)
        entity = _capture_if_authorized(gateway, payment, entity)
    except GatewayError as exc:
        # Not fatal: the money is safe and the webhook will finish the job.
        logger.warning("Verify deferred for order {}: {}", order_id, exc)
        db.rollback()
        return payment

    result = _apply_gateway_payment(db, order_id, entity)
    db.commit()
    return result or payment


# Applying what Razorpay reports


def _capture_if_authorized(gateway: RazorpayGateway, payment: PaymentORM, entity: Dict) -> Dict:
    if entity.get("status") != "authorized" or not _matches(payment, entity):
        return entity
    try:
        return gateway.capture_payment(entity["id"], payment.amount_paise, payment.currency)
    except GatewayError:
        # Usually another path captured first, so look before giving up.
        return gateway.fetch_payment(entity["id"])


def _matches(payment: PaymentORM, entity: Dict) -> bool:
    return (
        entity.get("order_id") == payment.razorpay_order_id
        and entity.get("amount") == payment.amount_paise
        and entity.get("currency") == payment.currency
    )


def _apply_gateway_payment(db: Session, order_id: str, entity: Dict) -> Optional[PaymentORM]:
    """Move one payment forward. The caller commits."""
    payment = PaymentCRUD.get_by_order(db, order_id, lock=True)
    if payment is None:
        return None

    gateway_status = entity.get("status")
    if gateway_status in ("captured", "refunded"):
        _mark_paid(db, payment, entity)
    elif gateway_status == "failed":
        _mark_failed(payment, entity)
    return payment


def _mark_paid(db: Session, payment: PaymentORM, entity: Dict) -> None:
    if payment.status == PaymentStatus.PAID or payment.status in REFUNDED_STATUSES:
        if payment.razorpay_payment_id != entity["id"]:
            logger.error(
                "Second captured payment {} on order {}: refund it manually",
                entity["id"],
                payment.razorpay_order_id,
            )
        return

    if not _matches(payment, entity):
        logger.critical("Captured payment {} does not match order {}", entity["id"], payment.id)
        payment.status = PaymentStatus.UNDER_REVIEW
        payment.razorpay_payment_id = entity["id"]
        payment.failure_reason = "Captured payment does not match the order"
        return

    # Allowed from failed and expired too: real money has been taken.
    payment.status = PaymentStatus.PAID
    payment.razorpay_payment_id = entity["id"]
    payment.method = entity.get("method")
    payment.failure_code = None
    payment.failure_reason = None
    payment.paid_at = _now()
    _grant_access(db, payment)
    logger.info("Payment {} paid", payment.id)


def _mark_failed(payment: PaymentORM, entity: Dict) -> None:
    # A late failure must never undo a success.
    if payment.status not in OPEN_STATUSES + (PaymentStatus.FAILED, PaymentStatus.EXPIRED):
        return
    payment.status = PaymentStatus.FAILED
    payment.razorpay_payment_id = None
    payment.method = entity.get("method")
    payment.failure_code = entity.get("error_code")
    payment.failure_reason = entity.get("error_description")


def _grant_access(db: Session, payment: PaymentORM) -> None:
    EntitlementCRUD.lock_user(db, payment.user_id)
    if EntitlementCRUD.get_by_payment(db, payment.id) is not None:
        return
    # Buying early extends the current period instead of wasting it.
    now = _now()
    starts_at = EntitlementCRUD.access_until(db, payment.user_id, now) or now
    db.add(
        EntitlementORM(
            user_id=payment.user_id,
            price_id=payment.price_id,
            payment_id=payment.id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(days=payment.duration_days),
            status="active",
        )
    )


def _apply_refund(db: Session, order_id: str, payment_entity: Dict) -> Optional[PaymentORM]:
    payment = _apply_gateway_payment(db, order_id, payment_entity)
    if payment is None or payment.status not in (PaymentStatus.PAID,) + REFUNDED_STATUSES:
        return payment

    # amount_refunded is a running total, so replays and reordering are harmless.
    refunded = max(payment.amount_refunded_paise, int(payment_entity.get("amount_refunded") or 0))
    payment.amount_refunded_paise = min(refunded, payment.amount_paise)
    if payment.amount_refunded_paise == payment.amount_paise:
        payment.status = PaymentStatus.REFUNDED
        entitlement = EntitlementCRUD.get_by_payment(db, payment.id)
        if entitlement is not None:
            entitlement.status = "revoked"
    elif payment.amount_refunded_paise > 0:
        payment.status = PaymentStatus.PARTIALLY_REFUNDED
    return payment


# Webhooks


def process_webhook(
    db: Session, gateway: RazorpayGateway, event_id: str, payload: Dict
) -> None:
    event_type = str(payload.get("event", "unknown"))
    event = WebhookEventCRUD.insert_and_lock(db, event_id, event_type, payload)
    if event.status in ("processed", "ignored"):
        db.commit()
        return

    try:
        handled = _dispatch_webhook(db, gateway, event_type, payload)
    except Exception as exc:
        db.rollback()
        event = WebhookEventCRUD.insert_and_lock(db, event_id, event_type, payload)
        event.status = "failed"
        event.error = str(exc)[:2000]
        db.commit()
        raise  # a 5xx makes Razorpay redeliver

    event.status = "processed" if handled else "ignored"
    event.error = None
    event.processed_at = _now()
    db.commit()


def _dispatch_webhook(
    db: Session, gateway: RazorpayGateway, event_type: str, payload: Dict
) -> bool:
    body = payload.get("payload", {})
    entity = body.get("payment", {}).get("entity")

    if event_type == "refund.processed":
        if not entity or not entity.get("order_id"):
            return False
        return _apply_refund(db, entity["order_id"], entity) is not None

    if event_type not in ("payment.authorized", "payment.captured", "payment.failed", "order.paid"):
        return False
    if not entity or not entity.get("order_id"):
        return False

    order_id = entity["order_id"]
    # Unknown orders are normal when one Razorpay account serves several apps.
    payment = PaymentCRUD.get_by_order(db, order_id)
    if payment is None:
        return False
    entity = _capture_if_authorized(gateway, payment, entity)
    _apply_gateway_payment(db, order_id, entity)
    return True


# Reconciliation


def reconcile(db: Session, gateway: RazorpayGateway, lookback_hours: int = 72) -> Dict[str, int]:
    """Safety net for missed webhooks and closed browsers. Run on a schedule."""
    now = _now()
    counts = {"checked": 0, "paid": 0, "expired": 0, "errors": 0}
    ids = [
        (row.id, row.user_id)
        for row in PaymentCRUD.list_for_reconcile(db, now - timedelta(hours=lookback_hours))
    ]
    db.commit()

    for payment_id, user_id in ids:
        payment = PaymentCRUD.get_for_user(db, payment_id, user_id)
        counts["checked"] += 1
        try:
            counts_key = _reconcile_one(db, gateway, payment, now)
            db.commit()
            if counts_key:
                counts[counts_key] += 1
        except GatewayError:
            db.rollback()
            counts["errors"] += 1
    return counts


def _reconcile_one(
    db: Session, gateway: RazorpayGateway, payment: PaymentORM, now: datetime
) -> Optional[str]:
    if payment.razorpay_order_id:
        for entity in gateway.order_payments(payment.razorpay_order_id):
            if entity.get("status") in ("authorized", "captured"):
                entity = _capture_if_authorized(gateway, payment, entity)
                _apply_gateway_payment(db, payment.razorpay_order_id, entity)
                if payment.status == PaymentStatus.PAID:
                    return "paid"

    if payment.status in OPEN_STATUSES and now - payment.created_at > timedelta(
        minutes=settings.ORDER_REUSE_MINUTES
    ):
        payment.status = PaymentStatus.EXPIRED
        return "expired"
    return None
