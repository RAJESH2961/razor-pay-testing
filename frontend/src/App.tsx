import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  ApiError,
  CheckoutResult,
  Me,
  Order,
  Payment,
  Plan,
  clearToken,
  createOrder,
  getMe,
  getPayment,
  getPayments,
  getPlans,
  hasToken,
  login,
  verifyPayment,
} from "./api";
import { openCheckout } from "./razorpay";

type Notice = { kind: "success" | "error" | "info"; text: string } | null;

const POLL_ATTEMPTS = 10;
const POLL_INTERVAL_MS = 3000;

const money = (paise: number, currency: string) =>
  new Intl.NumberFormat("en-IN", { style: "currency", currency }).format(paise / 100);

const date = (value: string) =>
  new Date(value).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });

const message = (error: unknown) =>
  error instanceof Error ? error.message : "Something went wrong";

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function Login({ onDone }: { onDone(): void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(email, password);
      onDone();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <h1>Sign in</h1>
      <form onSubmit={submit}>
        <label>
          Email
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Password
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <p className="notice error">{error}</p>}
        <button disabled={busy}>{busy ? "Signing in..." : "Sign in"}</button>
      </form>
    </main>
  );
}

function Store({ onSignOut }: { onSignOut(): void }) {
  const [me, setMe] = useState<Me | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [busyPlan, setBusyPlan] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);

  const refresh = useCallback(async () => {
    const [nextMe, nextPlans, nextPayments] = await Promise.all([
      getMe(),
      getPlans(),
      getPayments(),
    ]);
    setMe(nextMe);
    setPlans(nextPlans);
    setPayments(nextPayments);
  }, []);

  useEffect(() => {
    refresh().catch((error) => {
      if (error instanceof ApiError && error.status === 401) onSignOut();
      else setNotice({ kind: "error", text: message(error) });
    });
  }, [refresh, onSignOut]);

  // The webhook may finish the payment even when verify could not, so a slow
  // answer is reported as "processing", never as a failure.
  async function waitForResult(paymentId: string) {
    for (let attempt = 0; attempt < POLL_ATTEMPTS; attempt++) {
      try {
        if ((await getPayment(paymentId)).status === "paid") return true;
      } catch {
        // keep trying through a network blip
      }
      await wait(POLL_INTERVAL_MS);
    }
    return false;
  }

  async function finish(order: Order, result: CheckoutResult) {
    setNotice({ kind: "info", text: "Confirming your payment. Do not close this page." });
    let paid = false;
    try {
      paid = (await verifyPayment(result)).status === "paid";
    } catch (error) {
      if (error instanceof ApiError && error.status === 400) {
        setNotice({ kind: "error", text: "We could not confirm this payment. Contact support." });
        setBusyPlan(null);
        return;
      }
    }
    paid = paid || (await waitForResult(order.payment_id));
    setNotice(
      paid
        ? { kind: "success", text: `${order.plan_name} plan activated.` }
        : {
            kind: "info",
            text: "Payment received and still processing. Access will appear here shortly.",
          },
    );
    setBusyPlan(null);
    await refresh().catch(() => undefined);
  }

  async function buy(plan: Plan) {
    if (!me) return;
    setBusyPlan(plan.code);
    setNotice(null);
    try {
      const order = await createOrder(plan.code);
      await openCheckout(order, me, {
        onSuccess: (result) => void finish(order, result),
        onDismiss: () => {
          setBusyPlan(null);
          setNotice({ kind: "info", text: "Payment window closed." });
        },
        onAttemptFailed: (reason) =>
          setNotice({ kind: "error", text: `${reason}. You can try again.` }),
      });
    } catch (error) {
      setBusyPlan(null);
      setNotice({ kind: "error", text: message(error) });
    }
  }

  return (
    <main>
      <header>
        <div>
          <h1>Choose your plan</h1>
          {me && (
            <p>
              {me.full_name} ·{" "}
              {me.access_until ? `Access until ${date(me.access_until)}` : "No active plan"}
            </p>
          )}
        </div>
        <button className="ghost" onClick={onSignOut}>
          Sign out
        </button>
      </header>

      {notice && <p className={`notice ${notice.kind}`}>{notice.text}</p>}

      <section className="plans">
        {plans.map((plan) => (
          <article key={plan.code}>
            <h2>{plan.name}</h2>
            <p className="price">{money(plan.amount_paise, plan.currency)}</p>
            <p>{plan.duration_days} days of full access</p>
            <button disabled={busyPlan !== null} onClick={() => buy(plan)}>
              {busyPlan === plan.code ? "Processing..." : "Buy now"}
            </button>
          </article>
        ))}
      </section>

      <h2>Payment history</h2>
      {payments.length === 0 ? (
        <p>No payments yet.</p>
      ) : (
        <div className="table">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Amount</th>
                <th>Status</th>
                <th>Reference</th>
              </tr>
            </thead>
            <tbody>
              {payments.map((payment) => (
                <tr key={payment.id}>
                  <td>{date(payment.created_at)}</td>
                  <td>{money(payment.amount_paise, payment.currency)}</td>
                  <td>
                    <span className={`status ${payment.status}`}>
                      {payment.status.replace("_", " ")}
                    </span>
                  </td>
                  <td>{payment.razorpay_payment_id ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}

export default function App() {
  const [signedIn, setSignedIn] = useState(hasToken());
  const signOut = useCallback(() => {
    clearToken();
    setSignedIn(false);
  }, []);

  return signedIn ? <Store onSignOut={signOut} /> : <Login onDone={() => setSignedIn(true)} />;
}
