"""Razorpay test-mode execution adapter.

The policy engine is transport-agnostic: in evaluation it acts on the
simulator; in demo mode the SAME decisions drive real Razorpay test-mode
objects. Stdlib-only (urllib) — no SDK dependency.

Requires env vars (TEST keys only — this module refuses live keys):
    RZP_KEY_ID      rzp_test_...
    RZP_KEY_SECRET  ...

Surface used:
    POST /v1/payment_links     — recovery payment link for a failed debit
    GET  /v1/payment_links/:id — poll status (demo watches for test payment)
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request

BASE = "https://api.razorpay.com/v1"


class RazorpayTestClient:
    def __init__(self) -> None:
        key = os.environ.get("RZP_KEY_ID", "")
        secret = os.environ.get("RZP_KEY_SECRET", "")
        if not key or not secret:
            raise SystemExit(
                "Set RZP_KEY_ID / RZP_KEY_SECRET (test-mode keys from "
                "dashboard.razorpay.com -> Settings -> API Keys).")
        if not key.startswith("rzp_test_"):
            raise SystemExit(
                "Refusing non-test key: this project only ever touches "
                "test mode. Use an rzp_test_... key.")
        token = base64.b64encode(f"{key}:{secret}".encode()).decode()
        self._auth = f"Basic {token}"

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        req = urllib.request.Request(
            f"{BASE}{path}",
            data=json.dumps(payload).encode() if payload is not None else None,
            method=method,
            headers={"Authorization": self._auth, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    def create_recovery_link(self, amount_paise: int, mandate_id: str,
                             description: str, notes: dict) -> dict:
        return self._call("POST", "/payment_links", {
            "amount": amount_paise,
            "currency": "INR",
            "description": description,
            "reference_id": f"represent-{mandate_id}",
            "notes": notes,          # audit linkage: decision id, rule fired
            "notify": {"sms": False, "email": False},   # demo: no real sends
        })

    def get_link(self, link_id: str) -> dict:
        return self._call("GET", f"/payment_links/{link_id}")
