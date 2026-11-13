import json
from datetime import timedelta
from uuid import uuid4

from conftest import sign

from app.core.database import SessionLocal
from app.core.security import create_access_token
from app.modules.payments import service
from app.modules.payments.db.ORM import EntitlementORM, PaymentORM
from app.modules.users.db.ORM import UserORM


def create_order(client, auth, price):
    response = client.post("/api/v1/payments/orders", json={"price_code": price.code}, headers=auth)
    assert response.status_code == 201, response.text
    return response.json()


def verify(client, auth, order_id, payment_id, signature=None):
    signature = signature or sign(f"{order_id}|{payment_id}".encode(), "test-key-secret")
    return client.post(
        "/api/v1/payments/verify",
        json={
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        },
        headers=auth,
    )


def webhook(client, event, entity, event_id=None, extra=None):
    body = json.dumps(
        {"event": event, "payload": {"payment": {"entity": entity}, **(extra or {})}}
    ).encode()
    return client.post(
        "/api/v1/webhooks/razorpay",
        content=body,
        headers={
            "X-Razorpay-Signature": sign(body, "test-webhook-secret"),
            "X-Razorpay-Event-Id": event_id or uuid4().hex,
        },
    )


def stored(db, payment_id):
    db.expire_all()
    return db.get(PaymentORM, payment_id)


def grants(db, payment_id):
    db.expire_all()
    return db.query(EntitlementORM).filter(EntitlementORM.payment_id == payment_id).all()


def test_happy_path_grants_access(client, auth, gateway, db, price):
    order = create_order(client, auth, price)
    assert order["amount_paise"] == 29900

    paid = gateway.add_payment(order["razorpay_order_id"])
    response = verify(client, auth, order["razorpay_order_id"], paid["id"])

    assert response.json()["status"] == "paid"
    assert len(grants(db, order["payment_id"])) == 1
    assert client.get("/api/v1/me", headers=auth).json()["access_until"] is not None


def test_double_click_reuses_the_open_order(client, auth, gateway, price):
    first = create_order(client, auth, price)
    second = create_order(client, auth, price)
    assert first["payment_id"] == second["payment_id"]
    assert len(gateway.orders) == 1


def test_client_cannot_choose_the_amount(client, auth, gateway, price):
    response = client.post(
        "/api/v1/payments/orders",
        json={"price_code": price.code, "amount_paise": 1},
        headers=auth,
    )
    assert response.json()["amount_paise"] == 29900


def test_price_change_replaces_the_open_order(client, auth, db, price):
    first = create_order(client, auth, price)
    price.amount_paise = 39900
    db.commit()

    second = create_order(client, auth, price)

    assert second["amount_paise"] == 39900
    assert stored(db, first["payment_id"]).status == "expired"


def test_provider_outage_returns_502_and_allows_retry(client, auth, gateway, price):
    gateway.fail_create = True
    response = client.post("/api/v1/payments/orders", json={"price_code": price.code}, headers=auth)
    assert response.status_code == 502

    gateway.fail_create = False
    create_order(client, auth, price)


def test_forged_signature_changes_nothing(client, auth, gateway, db, price):
    order = create_order(client, auth, price)
    paid = gateway.add_payment(order["razorpay_order_id"])

    response = verify(client, auth, order["razorpay_order_id"], paid["id"], signature="0" * 64)

    assert response.status_code == 400
    assert stored(db, order["payment_id"]).status == "created"


def test_another_users_order_is_hidden(client, auth, gateway, db, price):
    order = create_order(client, auth, price)
    paid = gateway.add_payment(order["razorpay_order_id"])

    other = UserORM(email=f"{uuid4().hex}@example.com", full_name="Other", password_hash="x")
    db.add(other)
    db.commit()
    other_auth = {"Authorization": f"Bearer {create_access_token(other.id)}"}

    assert verify(client, other_auth, order["razorpay_order_id"], paid["id"]).status_code == 404
    assert client.get(f"/api/v1/payments/{order['payment_id']}", headers=other_auth).status_code == 404


def test_valid_signature_for_other_orders_payment_gives_no_access(client, auth, gateway, db, price):
    order = create_order(client, auth, price)
    other_payment = gateway.add_payment("order_someone_else")

    verify(client, auth, order["razorpay_order_id"], other_payment["id"])

    assert grants(db, order["payment_id"]) == []
    assert stored(db, order["payment_id"]).status == "under_review"


def test_wrong_amount_goes_to_review_without_access(client, auth, gateway, db, price):
    order = create_order(client, auth, price)
    paid = gateway.add_payment(order["razorpay_order_id"], amount=100)

    webhook(client, "payment.captured", paid)

    assert stored(db, order["payment_id"]).status == "under_review"
    assert grants(db, order["payment_id"]) == []


def test_verify_then_webhooks_grant_once(client, auth, gateway, db, price):
    order = create_order(client, auth, price)
    paid = gateway.add_payment(order["razorpay_order_id"])

    verify(client, auth, order["razorpay_order_id"], paid["id"])
    webhook(client, "payment.captured", paid)
    webhook(client, "order.paid", paid)

    assert len(grants(db, order["payment_id"])) == 1


def test_webhook_alone_completes_when_browser_closed(client, auth, gateway, db, price):
    order = create_order(client, auth, price)
    paid = gateway.add_payment(order["razorpay_order_id"])

    assert webhook(client, "payment.captured", paid).status_code == 200

    assert stored(db, order["payment_id"]).status == "paid"


def test_verify_survives_provider_outage_and_webhook_finishes(client, auth, gateway, db, price):
    order = create_order(client, auth, price)
    paid = gateway.add_payment(order["razorpay_order_id"])

    gateway.fail_fetch = True
    response = verify(client, auth, order["razorpay_order_id"], paid["id"])
    assert response.status_code == 200
    assert response.json()["status"] == "created"

    webhook(client, "payment.captured", paid)
    assert stored(db, order["payment_id"]).status == "paid"


def test_authorized_payment_is_captured_by_webhook(client, auth, gateway, db, price):
    order = create_order(client, auth, price)
    paid = gateway.add_payment(order["razorpay_order_id"], status="authorized")

    webhook(client, "payment.authorized", dict(paid))

    assert stored(db, order["payment_id"]).status == "paid"


def test_failure_then_retry_on_same_order_pays(client, auth, gateway, db, price):
    order = create_order(client, auth, price)
    failed = gateway.add_payment(
        order["razorpay_order_id"], status="failed", error_code="BAD", error_description="Declined"
    )
    webhook(client, "payment.failed", failed)
    assert stored(db, order["payment_id"]).status == "failed"

    paid = gateway.add_payment(order["razorpay_order_id"])
    webhook(client, "payment.captured", paid)

    payment = stored(db, order["payment_id"])
    assert payment.status == "paid"
    assert payment.failure_reason is None


def test_late_failure_does_not_undo_success(client, auth, gateway, db, price):
    order = create_order(client, auth, price)
    paid = gateway.add_payment(order["razorpay_order_id"])
    verify(client, auth, order["razorpay_order_id"], paid["id"])

    webhook(client, "payment.failed", {**paid, "status": "failed"})

    assert stored(db, order["payment_id"]).status == "paid"


def test_payment_after_local_expiry_still_grants(client, auth, gateway, db, price):
    order = create_order(client, auth, price)
    row = stored(db, order["payment_id"])
    row.status = "expired"
    db.commit()

    webhook(client, "payment.captured", gateway.add_payment(order["razorpay_order_id"]))

    assert stored(db, order["payment_id"]).status == "paid"


def test_webhook_bad_signature_rejected(client):
    response = client.post(
        "/api/v1/webhooks/razorpay",
        content=b'{"event":"order.paid"}',
        headers={"X-Razorpay-Signature": "0" * 64, "X-Razorpay-Event-Id": uuid4().hex},
    )
    assert response.status_code == 400


def test_unknown_order_is_acknowledged(client, gateway):
    assert webhook(client, "payment.captured", gateway.add_payment("order_other_app")).status_code == 200


def test_failed_webhook_is_retried(client, auth, gateway, db, price, monkeypatch):
    order = create_order(client, auth, price)
    paid = gateway.add_payment(order["razorpay_order_id"])
    event_id = uuid4().hex

    original = service._grant_access
    monkeypatch.setattr(service, "_grant_access", lambda *a: 1 / 0)
    failing = client.__class__(client.app, raise_server_exceptions=False)
    assert webhook(failing, "payment.captured", paid, event_id).status_code == 500
    assert stored(db, order["payment_id"]).status == "created"

    monkeypatch.setattr(service, "_grant_access", original)
    assert webhook(client, "payment.captured", paid, event_id).status_code == 200
    assert stored(db, order["payment_id"]).status == "paid"


def test_early_renewal_stacks_after_current_period(client, auth, gateway, db, price):
    first = create_order(client, auth, price)
    webhook(client, "payment.captured", gateway.add_payment(first["razorpay_order_id"]))
    second = create_order(client, auth, price)
    webhook(client, "payment.captured", gateway.add_payment(second["razorpay_order_id"]))

    first_grant = grants(db, first["payment_id"])[0]
    second_grant = grants(db, second["payment_id"])[0]
    assert second_grant.starts_at == first_grant.ends_at
    assert second_grant.ends_at - second_grant.starts_at == timedelta(days=30)


def test_partial_then_full_refund(client, auth, gateway, db, price):
    order = create_order(client, auth, price)
    paid = gateway.add_payment(order["razorpay_order_id"])
    webhook(client, "payment.captured", paid)

    webhook(client, "refund.processed", {**paid, "amount_refunded": 10000})
    assert stored(db, order["payment_id"]).status == "partially_refunded"
    assert grants(db, order["payment_id"])[0].status == "active"

    webhook(client, "refund.processed", {**paid, "status": "refunded", "amount_refunded": 29900})
    assert stored(db, order["payment_id"]).status == "refunded"
    assert grants(db, order["payment_id"])[0].status == "revoked"
    assert client.get("/api/v1/me", headers=auth).json()["access_until"] is None


def test_reconcile_recovers_missed_webhook(client, auth, gateway, db, price):
    order = create_order(client, auth, price)
    gateway.add_payment(order["razorpay_order_id"])

    with SessionLocal() as session:
        result = service.reconcile(session, gateway)

    assert result["paid"] >= 1
    assert stored(db, order["payment_id"]).status == "paid"


def test_api_requires_login(client, price):
    response = client.post("/api/v1/payments/orders", json={"price_code": price.code})
    assert response.status_code == 401
