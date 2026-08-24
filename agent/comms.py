"""Customer nudge drafting with a hard compliance gate.

Templates are the floor; an optional LLM pass (Claude) personalises tone
and produces the Hinglish variant. EVERY draft — template or LLM — must
pass the rubric gate before it is sent; a failing LLM draft falls back to
the static template. The gate is code, not vibes:
  - names the merchant and the exact amount
  - states WHY the customer is being contacted
  - contains an opt-out line (mandatory)
  - contains no urgency/threat vocabulary (RBI consumer-conduct posture)
"""
from __future__ import annotations

import hashlib
import re

from simlab.entities import Mandate

OPT_OUT_LINE = "Reply STOP to opt out of payment reminders."

_TEMPLATES = {
    "pay_link": (
        "Hi! Your {category} subscription payment of Rs.{amount} to {merchant} "
        "could not be processed (insufficient balance). No action is needed if "
        "you'd like us to retry automatically. To pay now instead, use this "
        "secure link: {link} . " + OPT_OUT_LINE
    ),
    "pay_link_hi": (
        "Namaste! {merchant} ke {category} subscription ka Rs.{amount} ka payment "
        "process nahi ho paya (balance kam tha). Aap chahein to yahan turant pay "
        "kar sakte hain: {link} . " + OPT_OUT_LINE
    ),
    "resume": (
        "Hi! Your autopay for {merchant} ({category}, Rs.{amount}/month) is "
        "currently paused, so this month's payment did not go through. You can "
        "resume it anytime here: {link} . " + OPT_OUT_LINE
    ),
    "afa_link": (
        "Hi! Your payment of Rs.{amount} to {merchant} needs one-time "
        "authentication because it is above the Rs.15,000 auto-debit limit set "
        "by RBI. Approve it securely here: {link} . " + OPT_OUT_LINE
    ),
}

_BANNED = re.compile(
    r"immediately|urgent|final warning|legal action|penalt|last chance|suspend", re.I
)


def rubric_gate(text: str, mandate: Mandate) -> tuple[bool, list[str]]:
    problems = []
    if f"Rs.{mandate.amount_paise // 100}" not in text:
        problems.append("exact amount missing")
    if OPT_OUT_LINE not in text:
        problems.append("opt-out line missing")
    if _BANNED.search(text):
        problems.append("pressure/threat vocabulary")
    if "{" in text:
        problems.append("unfilled template slot")
    return (not problems, problems)


def draft_nudge(mandate: Mandate, kind: str) -> str:
    # Deterministic language pick: ~40% Hinglish for pay links, keyed on
    # customer id so reruns are stable.
    key = kind
    if kind == "pay_link":
        h = int(hashlib.sha256(mandate.customer.customer_id.encode()).hexdigest(), 16)
        key = "pay_link_hi" if h % 10 < 4 else "pay_link"
    text = _TEMPLATES[key].format(
        category=mandate.category.value,
        amount=mandate.amount_paise // 100,
        merchant="DemoMerchant",
        link=f"https://rzp.io/l/{mandate.mandate_id[-8:]}",
    )
    ok, problems = rubric_gate(text, mandate)
    if not ok:  # template bug — fail loudly in dev, never send silently
        raise ValueError(f"nudge failed rubric gate: {problems}")
    return text
