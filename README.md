# Razorpay payments with FastAPI, React and Postgres

An edtech checkout: a user signs in, picks a weekly, monthly or yearly plan, pays through Razorpay, and gets access for that period. Everything is stored in the `payment_test` schema.

Plans are **prepaid passes**, paid once through the Razorpay Orders API. They do not auto-renew. Auto-debit would need the Razorpay Subscriptions API.

## How a payment flows

```
React                 FastAPI                     Razorpay         Postgres
  | POST /payments/orders |                           |                |
  |---------------------->| price read from DB        |                |
  |                       |--- row: pending -------------------------->|
  |                       |--- create order --------->|                |
  |                       |--- row: created -------------------------->|
  |<-- order id, key -----|                           |                |
  |--- Checkout popup: user pays -------------------->|                |
  |<-- payment id + signature ------------------------|                |
  | POST /payments/verify |                           |                |
  |---------------------->| check HMAC, fetch payment |                |
  |                       |--- paid + entitlement -------------------->|
  |                       |<-- webhook (also arrives) |                |
  |                       |--- no-op, already paid ------------------->|
```

Three paths can finish a payment: `verify` (browser), the webhook, and the reconcile job. All three use the same idempotent function, so the order they arrive in does not matter. The browser is only a fast path. If it closes after paying, the webhook still completes the purchase.

## Tables (`payment_test`)

| Table | Purpose |
|---|---|
| `users` | Customers |
| `prices` | Plans: code, amount in paise, duration in days |
| `payments` | One row per order. Keeps a snapshot of the amount and duration |
| `entitlements` | The access period a payment bought. Check this to unlock courses |
| `webhook_events` | Every webhook received, for de-duplication and audit |

Payment status: `pending` → `created` → `paid`, with `failed`, `expired`, `refunded`, `partially_refunded` and `under_review` as other states. `failed` and `expired` can still become `paid`, because money captured late must be honoured.

## What makes it production grade

| Risk | Protection |
|---|---|
| User edits the price in the browser | The client sends only a plan code; the amount comes from `prices` |
| Forged success callback | HMAC check with `compare_digest`, then the payment is fetched from Razorpay |
| Signature valid but for the wrong order or amount | Compared with our row; a mismatch becomes `under_review` with no access |
| Double click on Buy | Open order is reused; a partial unique index blocks duplicates |
| Webhook sent twice | Unique `event_id`, row lock, and unique `entitlements.payment_id` |
| Webhook handler crashes | Returns 500 so Razorpay redelivers; the event is marked `failed` until then |
| Browser closed, webhook missed | `scripts/reconcile.py` asks Razorpay about unfinished payments |
| Razorpay hangs | 4 second timeout, and no database lock is held while creating orders |
| Floating point money errors | Integer paise everywhere |
| Test keys in production | The app refuses to start |

## Run it

You need Python 3.11+, Node 20+, a Razorpay account in **Test Mode**, and a database user allowed to create a schema (or create `payment_test` first).

### 1. Get Razorpay keys

In the Razorpay Dashboard, switch to **Test Mode**, then open **Account & Settings → API Keys** and generate a key. The secret is shown only once.

### 2. Start the backend

From `backend/` in PowerShell:

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```

```bash
pip install -r requirements-dev.txt
```

```bash
copy .env.example .env
```

Edit `.env` with your AWS Postgres details, Razorpay keys and a `JWT_SECRET`. Generate the secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Create the tables, add demo data, and start the API:

```bash
alembic upgrade head
```

```bash
python -m scripts.seed
```

```bash
uvicorn app.main:app --reload --port 8000
```

Check `http://localhost:8000/health` and the API docs at `http://localhost:8000/docs`.

Seeding creates `asha@example.com`, `ravi@example.com` and `meera@example.com`, all using `SEED_USER_PASSWORD`, plus plans at ₹99, ₹299 and ₹2,499.

### 3. Start the frontend

From `frontend/`:

```bash
npm install
```

```bash
copy .env.example .env
```

```bash
npm run dev
```

Open `http://localhost:5173`, sign in, and buy a plan. Use the test card numbers or test UPI IDs listed in the Razorpay docs under **Test Card Details**. They change occasionally, so take them from there.

### 4. Connect webhooks

Razorpay cannot reach `localhost`, so expose the API with a tunnel:

```bash
cloudflared tunnel --url http://localhost:8000
```

In the Dashboard, open **Account & Settings → Webhooks → Add New Webhook**:

- URL: `https://<your-tunnel>/api/v1/webhooks/razorpay`
- Secret: the value of `RAZORPAY_WEBHOOK_SECRET`
- Events: `payment.authorized`, `payment.captured`, `payment.failed`, `order.paid`, `refund.processed`

### 5. Run the tests

From `backend/`. They start their own temporary Postgres and fake Razorpay, so they need no keys and never touch AWS:

```bash
pytest tests
```

## Practice these failures

1. **Closed browser:** pay, then close the tab as soon as the card succeeds. The row should still become `paid`.
2. **Forged verify:** call `/payments/verify` from `/docs` with a made-up signature. Expect 400.
3. **Replay:** use **Resend** on a delivered webhook in the Dashboard. There should be no second entitlement.
4. **Missed webhook:** stop the tunnel, pay, close the tab, then run `python -m scripts.reconcile`.
5. **Failed attempt:** use a failing test card, then retry in the same popup.
6. **Refund:** refund from the Dashboard. A full refund revokes access; a partial one keeps it.

Useful query:

```sql
SELECT status, amount_paise, razorpay_order_id, razorpay_payment_id, paid_at
FROM payment_test.payments ORDER BY created_at DESC LIMIT 10;
```

## Before real money

- Replace the demo login with your real auth and add rate limiting on `/auth/login` and order creation.
- Serve only over HTTPS and set `APP_ENV=production`, which also disables `/docs`.
- Keep secrets in AWS Secrets Manager or SSM, not in a file.
- Schedule `python -m scripts.reconcile` every 15 minutes.
- Alert on `under_review` payments, `failed` webhook events, and the "Second captured payment" log line. Those need a person.
- Use live keys and a **separate** webhook with its own secret. Test Mode and Live Mode do not share them.
- Prefer storing the token in an httpOnly cookie. This demo uses `sessionStorage` to stay simple.
- Disputes (`payment.dispute.*`) are not handled, and a full refund does not shorten plans stacked after it.
