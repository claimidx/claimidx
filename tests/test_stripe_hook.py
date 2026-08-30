import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from claimidx.api import create_app
from claimidx.stripe_hook import WebhookError, handle_payload, verify_signature


SECRET = "whsec_test_claimidx"


def _sign(payload: bytes, secret: str = SECRET, ts: int | None = None) -> str:
    stamped = int(time.time() if ts is None else ts)
    digest = hmac.new(secret.encode(), f"{stamped}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={stamped},v1={digest}"


def test_verify_accepts_matching_signature():
    payload = b'{"id":"evt_1","type":"ping"}'
    verify_signature(payload, _sign(payload), SECRET)


def test_verify_rejects_mismatch_and_stale():
    payload = b'{"id":"evt_1"}'
    with pytest.raises(WebhookError, match="mismatch"):
        verify_signature(payload, _sign(payload, "whsec_other"), SECRET)
    with pytest.raises(WebhookError, match="tolerance"):
        verify_signature(payload, _sign(payload, ts=1), SECRET, now=10_000)


def test_handle_projects_checkout_session():
    body = {
        "id": "evt_paid",
        "type": "checkout.session.completed",
        "livemode": True,
        "created": 1,
        "data": {
            "object": {
                "customer": "cus_1",
                "customer_email": "buyer@example.com",
                "subscription": "sub_1",
                "amount_total": 2900,
                "currency": "usd",
                "payment_status": "paid",
                "metadata": {"sku": "cloud_starter"},
            }
        },
    }
    payload = json.dumps(body).encode()
    out = handle_payload(payload, _sign(payload), SECRET)
    assert out["received"] is True
    assert out["paid"]["customer_email"] == "buyer@example.com"
    assert out["paid"]["amount_total"] == 2900
    assert out["paid"]["metadata"]["sku"] == "cloud_starter"


def test_fastapi_webhook_requires_secret_and_accepts_valid(monkeypatch, tmp_path):
    app = create_app(str(tmp_path / "ix.sqlite"))
    client = TestClient(app)
    payload = json.dumps({"id": "evt_x", "type": "ping"}).encode()
    missing = client.post("/api/stripe/webhook", content=payload, headers={"Stripe-Signature": _sign(payload)})
    assert missing.status_code == 503
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    bad = client.post("/api/stripe/webhook", content=payload, headers={"Stripe-Signature": "t=1,v1=nope"})
    assert bad.status_code == 400
    ok = client.post("/api/stripe/webhook", content=payload, headers={"Stripe-Signature": _sign(payload)})
    assert ok.status_code == 200
    assert ok.json()["id"] == "evt_x"
