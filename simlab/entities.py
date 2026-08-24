"""Core dataclasses shared by the simulator, the agent, and the auditor.

The agent NEVER sees fields marked latent — they exist so the world can
respond to actions. The auditor reads only the event log.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Cause(str, Enum):
    INSUFFICIENT_FUNDS = "insufficient_funds"
    TECHNICAL_DECLINE_BANK = "technical_decline_bank"
    TECHNICAL_DECLINE_PSP = "technical_decline_psp"
    MANDATE_PAUSED = "mandate_paused"
    MANDATE_REVOKED = "mandate_revoked"
    LIMIT_BREACH = "limit_breach"


# Causes for which any customer contact is prohibited: the customer has
# explicitly withdrawn consent. Contacting them is a compliance violation.
NO_CONTACT_CAUSES = frozenset({Cause.MANDATE_REVOKED})


class Category(str, Enum):
    STREAMING = "streaming"
    SAAS = "saas"
    EDTECH = "edtech"
    INSURANCE = "insurance"
    LENDING = "lending"


@dataclass
class Customer:
    customer_id: str
    salary_day: int              # day-of-month 1..30 when liquidity arrives
    # --- latent (world-only) ---
    recoverable: bool            # False = will never pay regardless of policy
    responsiveness: float        # 0..1 propensity to act on a nudge
    opt_out_prone: bool          # opts out after the second nudge


@dataclass
class Mandate:
    mandate_id: str
    customer: Customer
    amount_paise: int
    category: Category
    bank: str                    # e.g. "BANK03"
    due_day: int                 # simulation day of the scheduled debit
    umn: str                     # unique mandate number (cosmetic realism)


@dataclass
class FailureEvent:
    """The initial failed debit that puts a mandate into recovery."""
    mandate: Mandate
    fail_day: int
    cause: Cause                 # ground truth (label for classifier eval)
    error_code: str | None       # clean NPCI-style code, present ~70% of time
    narration: str               # raw bank narration string (always present)
    # TAT sub-case: money left the account but the txn failed. Reversal is
    # due T+5; the world schedules the actual reversal (possibly late).
    debited_not_reversed: bool = False
    actual_reversal_day: int | None = None


class ActionType(str, Enum):
    SEND_PDN = "send_pdn"                    # pre-debit notification
    RETRY_DEBIT = "retry_debit"              # re-presentation
    SEND_NUDGE = "send_nudge"                # payment-link message
    ESCALATE_HUMAN = "escalate_human"
    FILE_TAT_CLAIM = "file_tat_claim"        # UDIR compensation claim
    WRITE_OFF = "write_off"


class Outcome(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    NO_EFFECT = "no_effect"      # e.g. nudge sent, customer did not convert


@dataclass
class AuditRecord:
    """One decision = one append-only record. This is the audit trail."""
    seq: int
    policy: str
    mandate_id: str
    day: int
    slot: str
    action: ActionType
    reason: str                  # rule that fired
    rejected: list[str]          # alternatives considered and rejected
    checks: dict[str, bool]      # constraint checks the policy claims it ran
    outcome: Outcome | None = None
    amount_paise: int | None = None
    meta: dict = field(default_factory=dict)
