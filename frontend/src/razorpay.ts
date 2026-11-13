import type { CheckoutResult, Me, Order } from "./api";

const CHECKOUT_URL = "https://checkout.razorpay.com/v1/checkout.js";

declare global {
  interface Window {
    Razorpay?: new (options: Record<string, unknown>) => {
      open(): void;
      on(event: string, handler: (response: { error?: { description?: string } }) => void): void;
    };
  }
}

let scriptLoad: Promise<void> | null = null;

function loadCheckout(): Promise<void> {
  if (window.Razorpay) return Promise.resolve();
  scriptLoad ??= new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = CHECKOUT_URL;
    script.onload = () => resolve();
    script.onerror = () => {
      scriptLoad = null; // allow a retry after a network blip
      script.remove();
      reject(new Error("Could not load the payment window. Check your connection."));
    };
    document.body.appendChild(script);
  });
  return scriptLoad;
}

export interface CheckoutCallbacks {
  onSuccess(result: CheckoutResult): void;
  onDismiss(): void;
  onAttemptFailed(reason: string): void;
}

export async function openCheckout(order: Order, user: Me, callbacks: CheckoutCallbacks) {
  await loadCheckout();
  const checkout = new window.Razorpay!({
    key: order.razorpay_key_id,
    order_id: order.razorpay_order_id,
    amount: order.amount_paise,
    currency: order.currency,
    name: "EdTech Academy",
    description: `${order.plan_name} plan`,
    prefill: { name: user.full_name, email: user.email, contact: user.phone ?? "" },
    theme: { color: "#2f5bea" },
    handler: callbacks.onSuccess,
    modal: { ondismiss: callbacks.onDismiss, confirm_close: true },
  });
  // The window stays open here so the user can retry on the same order.
  checkout.on("payment.failed", (response) =>
    callbacks.onAttemptFailed(response.error?.description ?? "Payment failed"),
  );
  checkout.open();
}
