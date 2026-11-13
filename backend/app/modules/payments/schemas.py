from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    amount_paise: int
    currency: str
    duration_days: int


class CreateOrderRequest(BaseModel):
    # The client names a plan only. It never sends an amount.
    price_code: str = Field(min_length=1, max_length=50)


class CreateOrderResponse(BaseModel):
    payment_id: UUID
    razorpay_order_id: str
    razorpay_key_id: str
    amount_paise: int
    currency: str
    plan_name: str


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str = Field(max_length=64)
    razorpay_payment_id: str = Field(max_length=64)
    razorpay_signature: str = Field(max_length=128)


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    amount_paise: int
    amount_refunded_paise: int
    currency: str
    razorpay_order_id: Optional[str]
    razorpay_payment_id: Optional[str]
    method: Optional[str]
    failure_reason: Optional[str]
    paid_at: Optional[datetime]
    created_at: datetime
