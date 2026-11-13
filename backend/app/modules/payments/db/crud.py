from datetime import datetime
from typing import List, Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.modules.payments.db.ORM import (
    EntitlementORM,
    PaymentORM,
    PaymentStatus,
    PriceORM,
    WebhookEventORM,
)
from app.modules.users.db.ORM import UserORM

OPEN_STATUSES = (PaymentStatus.PENDING, PaymentStatus.CREATED)


class PriceCRUD:
    """CRUD operations for PriceORM model"""

    @staticmethod
    def list_active(db: Session) -> List[PriceORM]:
        return (
            db.query(PriceORM)
            .filter(PriceORM.is_active.is_(True))
            .order_by(PriceORM.amount_paise)
            .all()
        )

    @staticmethod
    def get_active_by_code(db: Session, code: str) -> Optional[PriceORM]:
        return (
            db.query(PriceORM)
            .filter(PriceORM.code == code, PriceORM.is_active.is_(True))
            .first()
        )


class PaymentCRUD:
    """CRUD operations for PaymentORM model"""

    @staticmethod
    def get_open_for_update(db: Session, user_id: UUID, price_id: UUID) -> Optional[PaymentORM]:
        return (
            db.query(PaymentORM)
            .filter(
                PaymentORM.user_id == user_id,
                PaymentORM.price_id == price_id,
                PaymentORM.status.in_(OPEN_STATUSES),
            )
            .with_for_update()
            .first()
        )

    @staticmethod
    def get_by_order(db: Session, order_id: str, lock: bool = False) -> Optional[PaymentORM]:
        query = db.query(PaymentORM).filter(PaymentORM.razorpay_order_id == order_id)
        if lock:
            query = query.with_for_update()
        return query.first()

    @staticmethod
    def get_for_user(db: Session, payment_id: UUID, user_id: UUID) -> Optional[PaymentORM]:
        return (
            db.query(PaymentORM)
            .filter(PaymentORM.id == payment_id, PaymentORM.user_id == user_id)
            .first()
        )

    @staticmethod
    def list_for_user(db: Session, user_id: UUID, limit: int = 50) -> List[PaymentORM]:
        return (
            db.query(PaymentORM)
            .filter(PaymentORM.user_id == user_id)
            .order_by(PaymentORM.created_at.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def list_for_reconcile(db: Session, since: datetime) -> List[PaymentORM]:
        return (
            db.query(PaymentORM)
            .filter(
                PaymentORM.created_at >= since,
                PaymentORM.status.in_(
                    OPEN_STATUSES + (PaymentStatus.FAILED, PaymentStatus.EXPIRED)
                ),
            )
            .order_by(PaymentORM.created_at)
            .all()
        )


class EntitlementCRUD:
    """CRUD operations for EntitlementORM model"""

    @staticmethod
    def access_until(db: Session, user_id: UUID, now: datetime) -> Optional[datetime]:
        ends_at = (
            db.query(func.max(EntitlementORM.ends_at))
            .filter(EntitlementORM.user_id == user_id, EntitlementORM.status == "active")
            .scalar()
        )
        return ends_at if ends_at and ends_at > now else None

    @staticmethod
    def get_by_payment(db: Session, payment_id: UUID) -> Optional[EntitlementORM]:
        return db.query(EntitlementORM).filter(EntitlementORM.payment_id == payment_id).first()

    @staticmethod
    def lock_user(db: Session, user_id: UUID) -> None:
        """Serialises grants per user so stacked periods cannot overlap."""
        db.query(UserORM.id).filter(UserORM.id == user_id).with_for_update().one()


class WebhookEventCRUD:
    """CRUD operations for WebhookEventORM model"""

    @staticmethod
    def insert_and_lock(db: Session, event_id: str, event_type: str, payload: dict) -> WebhookEventORM:
        db.execute(
            insert(WebhookEventORM)
            .values(event_id=event_id, event_type=event_type, payload=payload, status="received")
            .on_conflict_do_nothing(index_elements=["event_id"])
        )
        db.commit()
        # A concurrent redelivery waits here, then sees the finished status.
        return (
            db.query(WebhookEventORM)
            .filter(WebhookEventORM.event_id == event_id)
            .with_for_update()
            .one()
        )
