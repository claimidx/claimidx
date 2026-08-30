"""Stripe webhook signature check and paid-event projection.

Public endpoint lives on Cloudflare Pages (`/api/stripe/webhook`).
The home FastAPI route is the same contract for a later tenant host.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any


class WebhookError(ValueError):
    pass


def parse_signature(header: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (header or "").split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def verify_signature(payload: bytes, header: str, secret: str, *, tolerance: int = 300, now: int | None = None) -> None:
    if not secret:
        raise WebhookError("webhook secret missing")
    parts = parse_signature(header)
    ts = parts.get("t")
    v1 = parts.get("v1")
    if not ts or not v1:
        raise WebhookError("signature header missing t or v1")
    try:
        stamped = int(ts)
    except ValueError as e:
        raise WebhookError("signature timestamp is not an integer") from e
    clock = int(time.time() if now is None else now)
    if abs(clock - stamped) > tolerance:
        raise WebhookError("signature timestamp outside tolerance")
    expected = hmac.new(secret.encode("utf-8"), f"{ts}.".encode("utf-8") + payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, v1):
        raise WebhookError("signature mismatch")


def project_paid(event: dict[str, Any]) -> dict[str, Any] | None:
    """Fields a provisioner needs. No card data. No secret keys."""
    etype = str(event.get("type") or "")
    if etype not in {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "invoice.paid",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.payment_failed",
    }:
        return None
    obj = (event.get("data") or {}).get("object") or {}
    details = obj.get("customer_details") or {}
    return {
        "id": event.get("id"),
        "type": etype,
        "created": event.get("created"),
        "livemode": event.get("livemode"),
        "customer": obj.get("customer"),
        "customer_email": obj.get("customer_email") or details.get("email"),
        "subscription": obj.get("subscription") or obj.get("id") if etype.startswith("customer.subscription") else obj.get("subscription"),
        "amount_total": obj.get("amount_total") if "amount_total" in obj else obj.get("amount_paid"),
        "currency": obj.get("currency"),
        "status": obj.get("status") or obj.get("payment_status"),
        "metadata": obj.get("metadata") or {},
    }


def handle_payload(payload: bytes, header: str, secret: str, *, now: int | None = None) -> dict[str, Any]:
    verify_signature(payload, header, secret, now=now)
    try:
        event = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise WebhookError("payload is not json") from e
    if not isinstance(event, dict) or not event.get("id"):
        raise WebhookError("payload is not a stripe event")
    return {
        "received": True,
        "id": event.get("id"),
        "type": event.get("type"),
        "paid": project_paid(event),
    }
