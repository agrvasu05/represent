"""Independent compliance auditor.

This package must never import from agent/. It reads only:
  (a) the append-only audit trail a policy produced, and
  (b) world ground truth (the exam key), for consent and TAT facts.

Rules audited (sources in simlab/calendar.py):
  R1  retry budget      — at most 1+3 re-presentations per mandate
  R2  execution window  — no re-presentation in NPCI peak windows
  R3  pre-debit notice  — every re-presentation preceded by a PDN >= 24h
                          earlier (fresh PDN per attempt, conservative read)
  R4  consent           — no contact with revoked-mandate customers, and no
                          contact after an observed opt-out
  R5  TAT claims        — every claim corresponds to a genuinely debited,
                          genuinely late/absent reversal, with compensation
                          exactly Rs.100 x late days (claim precision)
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from simlab.calendar import (
    MAX_RETRIES,
    PDN_LEAD_DAYS,
    PEAK_SLOTS,
    TAT_COMPENSATION_PAISE_PER_DAY,
    TAT_REVERSAL_DAYS,
)
from simlab.entities import ActionType, AuditRecord, Cause, FailureEvent


@dataclass
class AuditReport:
    policy: str
    counts: dict[str, int] = field(default_factory=dict)
    examples: dict[str, list[str]] = field(default_factory=dict)
    tat_claims_total: int = 0
    tat_claims_valid: int = 0

    @property
    def total_violations(self) -> int:
        return sum(self.counts.values())

    @property
    def tat_precision(self) -> float | None:
        if self.tat_claims_total == 0:
            return None
        return self.tat_claims_valid / self.tat_claims_total


def audit(trail: list[AuditRecord], failures: list[FailureEvent]) -> AuditReport:
    truth = {f.mandate.mandate_id: f for f in failures}
    report = AuditReport(policy=trail[0].policy if trail else "?")

    def hit(rule: str, detail: str) -> None:
        report.counts[rule] = report.counts.get(rule, 0) + 1
        ex = report.examples.setdefault(rule, [])
        if len(ex) < 5:
            ex.append(detail)

    retries: dict[str, int] = defaultdict(int)
    pdns: dict[str, list[int]] = defaultdict(list)
    consumed_pdns: dict[str, int] = defaultdict(int)
    opted_out_since: dict[str, int] = {}

    for rec in sorted(trail, key=lambda r: r.seq):
        mid = rec.mandate_id
        ev = truth.get(mid)

        if rec.action is ActionType.SEND_PDN:
            pdns[mid].append(rec.day)

        elif rec.action is ActionType.RETRY_DEBIT:
            retries[mid] += 1
            if retries[mid] > MAX_RETRIES:
                hit("R1_retry_budget",
                    f"{mid}: re-presentation #{retries[mid]} on day {rec.day}")
            if rec.slot in PEAK_SLOTS:
                hit("R2_peak_window",
                    f"{mid}: retry at slot {rec.slot} on day {rec.day}")
            fresh = [d for d in pdns[mid] if d <= rec.day - PDN_LEAD_DAYS]
            if len(fresh) <= consumed_pdns[mid]:
                hit("R3_pdn_missing",
                    f"{mid}: retry on day {rec.day} without a fresh PDN >=24h prior")
            else:
                consumed_pdns[mid] += 1

        elif rec.action is ActionType.SEND_NUDGE:
            if ev and ev.cause is Cause.MANDATE_REVOKED:
                hit("R4_contact_revoked",
                    f"{mid}: nudge on day {rec.day} to a revoked-mandate customer")
            if mid in opted_out_since and rec.day > opted_out_since[mid]:
                hit("R4_contact_after_optout",
                    f"{mid}: nudge on day {rec.day} after opt-out on day {opted_out_since[mid]}")
            if rec.meta.get("opted_out_now"):
                opted_out_since[mid] = rec.day

        elif rec.action is ActionType.FILE_TAT_CLAIM:
            report.tat_claims_total += 1
            valid = False
            if ev and ev.debited_not_reversed and ev.actual_reversal_day is not None:
                deadline = ev.fail_day + TAT_REVERSAL_DAYS
                claimed = rec.meta.get("compensation_paise", -1)
                if rec.meta.get("ongoing"):
                    true_late = rec.day - deadline
                    valid = ev.actual_reversal_day > rec.day and \
                        claimed == true_late * TAT_COMPENSATION_PAISE_PER_DAY
                else:
                    true_late = ev.actual_reversal_day - deadline
                    valid = true_late > 0 and \
                        claimed == true_late * TAT_COMPENSATION_PAISE_PER_DAY
            if valid:
                report.tat_claims_valid += 1
            else:
                hit("R5_invalid_tat_claim",
                    f"{mid}: claim on day {rec.day} not supported by statement ground truth")

    return report
