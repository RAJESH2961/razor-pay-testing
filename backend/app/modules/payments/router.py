import hashlib
import json
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from loguru import logger
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.modules.payments import service
from app.modules.payments.db.crud import PaymentCRUD, PriceCRUD
from app.modules.payments.gateway import RazorpayGateway, get_gateway
from app.modules.payments.schemas import (
    CreateOrderRequest,
    CreateOrderResponse,
    PaymentResponse,
    PlanResponse,
    VerifyPaymentRequest,
)
from app.modules.users.db.ORM import UserORM

router = APIRouter(tags=["payments"])


@router.get("/plans", response_model=List[PlanResponse])
def list_plans(db: Session = Depends(get_db)):
    return PriceCRUD.list_active(db)


@router.post("/payments/orders", response_model=CreateOrderResponse, status_code=201)
def create_order(
    body: CreateOrderRequest,
    user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
    gateway: RazorpayGateway = Depends(get_gateway),
):
    payment, price = service.create_checkout(db, gateway, user, body.price_code)
    return CreateOrderResponse(
        payment_id=payment.id,
        razorpay_order_id=payment.razorpay_order_id,
        razorpay_key_id=settings.RAZORPAY_KEY_ID,
        amount_paise=payment.amount_paise,
        currency=payment.currency,
        plan_name=price.name,
    )


@router.post("/payments/verify", response_model=PaymentResponse)
def verify_payment(
    body: VerifyPaymentRequest,
    user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
    gateway: RazorpayGateway = Depends(get_gateway),
):
    return service.verify_checkout(
        db,
        gateway,
        user,
        body.razorpay_order_id,
        body.razorpay_payment_id,
        body.razorpay_signature,
    )


@router.get("/payments", response_model=List[PaymentResponse])
def list_payments(user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    return PaymentCRUD.list_for_user(db, user.id)


@router.get("/payments/{payment_id}", response_model=PaymentResponse)
def get_payment(
    payment_id: UUID,
    user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payment = PaymentCRUD.get_for_user(db, payment_id, user.id)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    return payment


async def _raw_body(request: Request) -> bytes:
    return await request.body()


@router.post("/webhooks/razorpay")
def razorpay_webhook(
    raw_body: bytes = Depends(_raw_body),
    signature: Optional[str] = Header(default=None, alias="X-Razorpay-Signature"),
    event_id: Optional[str] = Header(default=None, alias="X-Razorpay-Event-Id"),
    db: Session = Depends(get_db),
    gateway: RazorpayGateway = Depends(get_gateway),
):
    # Authenticated by signature, not JWT. Check it before parsing anything.
    if not signature or not gateway.webhook_signature_valid(raw_body, signature):
        logger.warning("Rejected webhook with invalid signature")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid signature")

    payload = json.loads(raw_body)
    service.process_webhook(
        db, gateway, event_id or hashlib.sha256(raw_body).hexdigest(), payload
    )
    return {"status": "ok"}
