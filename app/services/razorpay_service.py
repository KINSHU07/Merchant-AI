"""
Razorpay test-mode integration — the ONLY place in the codebase that talks
to Razorpay. Test mode only; must never be pointed at live keys (see
app/config.py). Every function here does real work or raises a clear
error — it never fabricates an order id or pretends success.
"""
from __future__ import annotations

import razorpay

from app.config import settings

_client: razorpay.Client | None = None


class RazorpayNotConfigured(Exception):
    """Raised when Razorpay credentials are missing. Callers must treat
    this as a real failure — never silently skip payment creation."""


def _get_client() -> razorpay.Client:
    global _client
    if _client is None:
        if not settings.razorpay_key_id or not settings.razorpay_key_secret:
            raise RazorpayNotConfigured(
                "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not set. Get test-mode "
                "keys from the Razorpay dashboard and add them to backend/.env"
            )
        _client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
    return _client


def create_order(*, amount: float, currency: str, receipt: str, notes: dict | None = None) -> dict:
    """
    Creates a real Razorpay order in TEST MODE. Amount must be in the
    smallest currency unit (paise for INR) per Razorpay's API — the caller
    passes rupees, this function does the conversion once, in one place.

    Returns the raw Razorpay order dict (contains 'id', 'status', etc.).
    Raises on any API failure — never returns a fabricated order.
    """
    client = _get_client()
    amount_in_paise = int(round(amount * 100))
    return client.order.create(
        {
            "amount": amount_in_paise,
            "currency": currency,
            "receipt": receipt,
            "notes": notes or {},
            "payment_capture": 1,
        }
    )