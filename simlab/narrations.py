"""Bank narration strings for failure events.

~70% of events carry a clean NPCI-style error code (deterministically
mappable). The rest carry only a messy free-text narration — truncated,
bank-jargon, occasionally Hinglish — which is what the LLM leg of the
classifier exists for. Templates are stylized from real UPI/NACH narration
patterns (return code 01 = insufficient funds on NACH, etc.).
"""
from __future__ import annotations

import random

from .entities import Cause

# Clean machine codes by cause (subset styled on NPCI/NACH return codes).
CODES: dict[Cause, list[str]] = {
    Cause.INSUFFICIENT_FUNDS: ["U301-01", "NACH-RC01", "ZM-BALANCE"],
    Cause.TECHNICAL_DECLINE_BANK: ["U28-BANK", "U69-CBS-DOWN", "91-ISSUER-TO"],
    Cause.TECHNICAL_DECLINE_PSP: ["U16-PSP", "U91-SWITCH-TO"],
    Cause.MANDATE_PAUSED: ["MND-PAUSE-CUST", "RC-AP04"],
    Cause.MANDATE_REVOKED: ["MND-REVOKE-CUST", "RC-AP05"],
    Cause.LIMIT_BREACH: ["U302-LIMIT", "RC-AM21"],
}

MESSY: dict[Cause, list[str]] = {
    Cause.INSUFFICIENT_FUNDS: [
        "DR FAILED ACBAL INSUFF A/C XX{tail}",
        "RTN reason: funds not arranged in acct, retry after salary credit",
        "balance kam hai account me - debit reject",
        "TXN DECLINED INSUFFICIENT BAL AS ON DT",
        "ach dr return 01-insuff funds see memo",
    ],
    Cause.TECHNICAL_DECLINE_BANK: [
        "ISSUER CBS TIMEOUT UPI DEBIT NOT CONFIRMED",
        "bank server down tha, txn status unknown/failed",
        "DEBIT PROCESSED CREDIT NOT RECD - TECH DECLINE AT REMITTER BANK",
        "U69 connection timed out at issuer switch",
    ],
    Cause.TECHNICAL_DECLINE_PSP: [
        "psp gateway 5xx during collect exec",
        "AGGREGATOR SWITCH TIMEOUT BEFORE BANK LEG",
    ],
    Cause.MANDATE_PAUSED: [
        "customer ne autopay pause kiya hai app se",
        "MANDATE STATE=PAUSED EXEC SKIPPED",
        "user paused e-mandate via psp app, resume unknown",
    ],
    Cause.MANDATE_REVOKED: [
        "MANDATE CANCELLED BY PAYER W.E.F. {day}",
        "customer revoked autopay consent - do not represent",
        "umn deregistered by user, mandate closed",
    ],
    Cause.LIMIT_BREACH: [
        "PER TXN LIMIT EXCEEDED FOR AFA-FREE DEBIT",
        "amount above mandate cap, needs additional factor auth",
    ],
}


# Cause-agnostic junk narrations: real return files contain these. No
# keyword can resolve them; the classifier is EXPECTED to gate them to a
# human. Applies to any cause, incl. revoked — gating means no contact,
# so the conservative default stays compliant.
AMBIGUOUS = [
    "txn failed pls chk with bank",
    "return as per bank memo dt {day}/08 ref annexure B",
    "debit unsuccessful - refer branch",
    "U99-UNKNOWN processing error see attachment",
    "failed. reason not specified by remitter bank",
]

# Misleading narrations: keywords point at the WRONG cause; a keyword
# heuristic mislabels these, a language model usually does not. Excluded
# for revoked (revocation notices are formulaic in practice, and a
# mislabel there would mean contacting a customer who withdrew consent).
TRICKY: dict[Cause, list[str]] = {
    Cause.TECHNICAL_DECLINE_BANK: [
        "customer balance sufficient but debit failed at bank end",
        "funds available; issuer системы error - timeout",   # noisy vendor junk
    ],
    Cause.INSUFFICIENT_FUNDS: [
        "no bank error; a/c had insufficient clear funds after hold",
    ],
    Cause.MANDATE_PAUSED: [
        "exec not attempted this cycle per customer app setting",
    ],
    Cause.LIMIT_BREACH: [
        "txn value outside permissible band for standing instruction",
    ],
}


def make_narration(cause: Cause, rng: random.Random) -> tuple[str | None, str]:
    """Return (error_code | None, narration).

    ~70% carry a clean machine code; of the code-less remainder, some are
    genuinely ambiguous (gate-to-human material) and some are misleading
    (the LLM-vs-keyword gap). Rates: 5% ambiguous, 5% tricky, rest clean.
    """
    r = rng.random()
    if r < 0.05:
        text = rng.choice(AMBIGUOUS).format(day=rng.randint(1, 28))
        return None, text
    if r < 0.10 and cause in TRICKY:
        return None, rng.choice(TRICKY[cause])
    has_code = rng.random() < 0.70
    code = rng.choice(CODES[cause]) if has_code else None
    text = rng.choice(MESSY[cause]).format(
        tail=rng.randint(1000, 9999), day=rng.randint(1, 28)
    )
    if has_code:
        text = f"{code} | {text}"
    return code, text
