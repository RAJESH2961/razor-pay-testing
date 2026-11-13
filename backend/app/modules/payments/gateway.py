"""The only module that talks to Razorpay, which also makes it easy to fake in tests."""
import hashlib
import hmac
from functools import lru_cache
from typing import Any, Dict, List

import razorpay
import requests
from loguru import logger

from app.core.config import settings


class GatewayError(Exception):
    """Razorpay could not be reached or rejected the request."""


class _TimeoutSession(requests.Session):
    """The SDK sets no timeout, and a hung call would pin a worker thread."""

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", settings.RAZORPAY_TIMEOUT_SECONDS)
        return super().request(*args, **kwargs)


def _hmac_matches(message: bytes, secret: str, signature: str) -> bool:
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    # compare_digest takes constant time, so timing leaks nothing about the signature.
    return hmac.compare_digest(expected.encode(), signature.encode())


class RazorpayGateway:
    def __init__(self) -> None:
        self._client = razorpay.Client(
            session=_TimeoutSession(),
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
        )

    def _call(self, action: str, fn, *args) -> Any:
        try:
            return fn(*args)
        except (razorpay.errors.BadRequestError,
                razorpay.errors.GatewayError,
                razorpay.errors.ServerError,
                razorpay.errors.SignatureVerificationError,
                requests.RequestException) as exc:
            logger.warning("Razorpay {} failed: {}", action, exc)
            raise GatewayError(str(exc)) from exc

    def create_order(self, amount_paise: int, currency: str, receipt: str, notes: Dict[str, str]) -> Dict:
        return self._call(
            "order.create",
            self._client.order.create,
            {
                "amount": amount_paise,
                "currency": currency,
                "receipt": receipt,
                "notes": notes,
                "payment_capture": 1,
            },
        )

    def fetch_payment(self, payment_id: str) -> Dict:
        return self._call("payment.fetch", self._client.payment.fetch, payment_id)

    def capture_payment(self, payment_id: str, amount_paise: int, currency: str) -> Dict:
        return self._call(
            "payment.capture",
            self._client.payment.capture,
            payment_id,
            amount_paise,
            {"currency": currency},
        )

    def order_payments(self, order_id: str) -> List[Dict]:
        return self._call("order.payments", self._client.order.payments, order_id).get("items", [])

    def checkout_signature_valid(self, order_id: str, payment_id: str, signature: str) -> bool:
        message = f"{order_id}|{payment_id}".encode()
        return _hmac_matches(message, settings.RAZORPAY_KEY_SECRET, signature)

    def webhook_signature_valid(self, raw_body: bytes, signature: str) -> bool:
        # Must be the exact bytes received: re-serialised JSON would not match.
        return _hmac_matches(raw_body, settings.RAZORPAY_WEBHOOK_SECRET, signature)


@lru_cache
def get_gateway() -> RazorpayGateway:
    return RazorpayGateway()
