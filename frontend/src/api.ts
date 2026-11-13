const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1";
const TOKEN_KEY = "access_token";

export interface Plan {
  code: string;
  name: string;
  amount_paise: number;
  currency: string;
  duration_days: number;
}

export interface Me {
  id: string;
  email: string;
  full_name: string;
  phone: string | null;
  access_until: string | null;
}

export interface Order {
  payment_id: string;
  razorpay_order_id: string;
  razorpay_key_id: string;
  amount_paise: number;
  currency: string;
  plan_name: string;
}

export interface Payment {
  id: string;
  status: string;
  amount_paise: number;
  amount_refunded_paise: number;
  currency: string;
  razorpay_order_id: string | null;
  razorpay_payment_id: string | null;
  method: string | null;
  failure_reason: string | null;
  paid_at: string | null;
  created_at: string;
}

export interface CheckoutResult {
  razorpay_order_id: string;
  razorpay_payment_id: string;
  razorpay_signature: string;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export const hasToken = () => sessionStorage.getItem(TOKEN_KEY) !== null;
export const clearToken = () => sessionStorage.removeItem(TOKEN_KEY);

async function request<T>(path: string, body?: unknown): Promise<T> {
  const token = sessionStorage.getItem(TOKEN_KEY);
  const response = await fetch(`${API_URL}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    const detail = typeof error?.detail === "string" ? error.detail : "Something went wrong";
    throw new ApiError(response.status, detail);
  }
  return response.json();
}

export async function login(email: string, password: string) {
  const { access_token } = await request<{ access_token: string }>("/auth/login", {
    email,
    password,
  });
  sessionStorage.setItem(TOKEN_KEY, access_token);
}

export const getMe = () => request<Me>("/me");
export const getPlans = () => request<Plan[]>("/plans");
export const getPayments = () => request<Payment[]>("/payments");
export const getPayment = (id: string) => request<Payment>(`/payments/${id}`);
export const createOrder = (priceCode: string) =>
  request<Order>("/payments/orders", { price_code: priceCode });
export const verifyPayment = (result: CheckoutResult) =>
  request<Payment>("/payments/verify", result);
