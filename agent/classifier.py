"""Hybrid failure-cause classifier.

Three legs, cheapest first:
  1. CODE   — deterministic map for clean NPCI-style error codes. Free,
              exact, covers ~70% of events. No model gets to overrule it.
  2. LLM    — Claude classifies messy free-text narrations (Hinglish
              included) into the taxonomy, with a confidence score.
              Responses are cached to disk keyed by narration hash, so
              `make eval` reproduces bit-for-bit without an API key.
  3. HEURISTIC — keyword fallback when no key and no cache. Its accuracy is
              reported separately; it exists so the pipeline never blocks.

Confidence gate: any classification below CONF_GATE is returned as
UNKNOWN, which the policy maps to the most conservative plan (no contact,
escalate). Misreading "revoked" as "insufficient funds" nudges a customer
who withdrew consent — a compliance breach — so uncertainty must fail safe.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from simlab.entities import Cause

CONF_GATE = 0.70
UNKNOWN = "unknown"

_CODE_MAP = {
    "U301-01": Cause.INSUFFICIENT_FUNDS, "NACH-RC01": Cause.INSUFFICIENT_FUNDS,
    "ZM-BALANCE": Cause.INSUFFICIENT_FUNDS,
    "U28-BANK": Cause.TECHNICAL_DECLINE_BANK, "U69-CBS-DOWN": Cause.TECHNICAL_DECLINE_BANK,
    "91-ISSUER-TO": Cause.TECHNICAL_DECLINE_BANK,
    "U16-PSP": Cause.TECHNICAL_DECLINE_PSP, "U91-SWITCH-TO": Cause.TECHNICAL_DECLINE_PSP,
    "MND-PAUSE-CUST": Cause.MANDATE_PAUSED, "RC-AP04": Cause.MANDATE_PAUSED,
    "MND-REVOKE-CUST": Cause.MANDATE_REVOKED, "RC-AP05": Cause.MANDATE_REVOKED,
    "U302-LIMIT": Cause.LIMIT_BREACH, "RC-AM21": Cause.LIMIT_BREACH,
}

_HEURISTIC_PATTERNS: list[tuple[str, Cause]] = [
    (r"revok|cancel+ed by payer|deregister|do not represent|mandate closed", Cause.MANDATE_REVOKED),
    (r"pause|resume unknown|exec skipped", Cause.MANDATE_PAUSED),
    (r"limit|cap|additional factor|afa", Cause.LIMIT_BREACH),
    (r"insuff|balance|bal\b|funds not arranged|kam hai|salary credit", Cause.INSUFFICIENT_FUNDS),
    (r"psp|aggregator|gateway 5xx", Cause.TECHNICAL_DECLINE_PSP),
    (r"cbs|issuer|server down|timeout|timed out|tech decline|credit not rec", Cause.TECHNICAL_DECLINE_BANK),
]

# Narration markers for the TAT sub-case: money left the account.
_DEBIT_MARKERS = re.compile(r"debit processed|debited|credit not rec", re.I)

_LLM_SYSTEM = """You classify Indian bank/UPI mandate-failure narrations.
Reply with STRICT JSON: {"cause": <one of insufficient_funds|technical_decline_bank|technical_decline_psp|mandate_paused|mandate_revoked|limit_breach>, "confidence": <0..1>, "debited": <true|false>}
"debited" = the narration indicates the customer's account WAS debited but the transaction failed (money awaiting reversal).
Narrations mix English and Hindi (Hinglish). If genuinely ambiguous, lower confidence rather than guessing."""


@dataclass
class Classification:
    cause: str            # Cause value or "unknown"
    confidence: float
    method: str           # code | llm | heuristic | gated
    debited_flag: bool    # TAT sub-case suspicion


class HybridClassifier:
    def __init__(self, cache_path: str | Path = "out/llm_cache.json", use_llm: bool | None = None):
        self.cache_path = Path(cache_path)
        self.cache: dict[str, dict] = {}
        if self.cache_path.exists():
            self.cache = json.loads(self.cache_path.read_text())
        if use_llm is None:
            use_llm = bool(os.environ.get("ANTHROPIC_API_KEY"))
        self.use_llm = use_llm
        self._client = None

    # ------------------------------------------------------------- public
    def classify(self, error_code: str | None, narration: str) -> Classification:
        debited = bool(_DEBIT_MARKERS.search(narration))
        if error_code and error_code in _CODE_MAP:
            return Classification(_CODE_MAP[error_code].value, 1.0, "code", debited)

        result = self._from_cache_or_llm(narration) if (self.use_llm or self._in_cache(narration)) else None
        if result is None:
            result = self._heuristic(narration)

        if result.confidence < CONF_GATE:
            return Classification(UNKNOWN, result.confidence, "gated", debited)
        return Classification(result.cause, result.confidence, result.method, debited or result.debited_flag)

    # ------------------------------------------------------------ private
    def _key(self, narration: str) -> str:
        return hashlib.sha256(narration.encode()).hexdigest()[:24]

    def _in_cache(self, narration: str) -> bool:
        return self._key(narration) in self.cache

    def _from_cache_or_llm(self, narration: str) -> Classification | None:
        k = self._key(narration)
        if k in self.cache:
            d = self.cache[k]
            return Classification(d["cause"], d["confidence"], "llm", d.get("debited", False))
        if not self.use_llm:
            return None
        try:
            if self._client is None:
                import anthropic
                self._client = anthropic.Anthropic()
            msg = self._client.messages.create(
                model=os.environ.get("REPRESENT_MODEL", "claude-sonnet-5"),
                max_tokens=200,
                system=_LLM_SYSTEM,
                messages=[{"role": "user", "content": narration}],
            )
            d = json.loads(msg.content[0].text)
            d = {"cause": d["cause"], "confidence": float(d["confidence"]),
                 "debited": bool(d.get("debited", False))}
            self.cache[k] = d
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self.cache, indent=1, sort_keys=True))
            return Classification(d["cause"], d["confidence"], "llm", d["debited"])
        except Exception:
            return None  # fall through to heuristic; never block the run

    def _heuristic(self, narration: str) -> Classification:
        low = narration.lower()
        for pattern, cause in _HEURISTIC_PATTERNS:
            if re.search(pattern, low):
                # keyword matches are decent but not certain; 0.78 keeps them
                # above the gate while honestly below code/LLM certainty
                return Classification(cause.value, 0.78, "heuristic",
                                      bool(_DEBIT_MARKERS.search(narration)))
        return Classification(UNKNOWN, 0.30, "heuristic", False)
