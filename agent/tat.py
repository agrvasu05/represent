"""TAT compensation tracker (RBI DPSS.CO.PD No.629, Sep 2019).

For the technical-decline sub-case where the customer's account WAS
debited but the transaction failed, the bank must auto-reverse within
T+5 days; beyond that the bank owes Rs.100/day, suo motu.

The tracker:
  - watches cases the classifier flagged `debited` (never ground truth),
  - polls the bank-statement API for the reversal each day,
  - when a reversal lands late, computes the accrued compensation and
    files a UDIR-style claim with the full evidence trail,
  - if the reversal is still pending well past the deadline, files for
    the accrual to date and escalates to human follow-up.

Filing discipline (the false-positive cost): a claim is filed ONLY on an
observed late/absent reversal — never on a prediction. Claim precision is
independently recomputed by the auditor against world ground truth.
"""
from __future__ import annotations

from simlab.calendar import (
    TAT_COMPENSATION_PAISE_PER_DAY,
    TAT_REVERSAL_DAYS,
    When,
)
from simlab.engine import Action, WorldAPI
from simlab.entities import ActionType

PENDING_GRACE_DAYS = 10   # file for ongoing delay this long past deadline


class TatTracker:
    def __init__(self) -> None:
        self.filed: set[str] = set()

    def poll(self, day: int, api: WorldAPI, cases: dict) -> None:
        for mid, case in cases.items():
            if mid in self.filed:
                continue
            # Poll the statement for every bank-side technical decline, not
            # only narration-flagged ones — narrations under-report debits.
            # check_reversal() answers from the statement; "n/a" = no debit.
            if not (case.debited_flag
                    or case.cause == "technical_decline_bank"):
                continue
            deadline = case.ev.fail_day + TAT_REVERSAL_DAYS
            status = api.check_reversal(mid)
            if status == "n/a":
                continue  # classifier over-flagged; statement shows no debit
            if status == "reversed":
                observed = api.reversal_observed_day(mid)
                if observed is not None and observed > deadline:
                    late_days = observed - deadline
                    self._file(api, case, day, late_days, ongoing=False)
            elif status == "pending" and day >= deadline + PENDING_GRACE_DAYS:
                self._file(api, case, day, day - deadline, ongoing=True)

    def _file(self, api: WorldAPI, case, day: int, late_days: int, ongoing: bool) -> None:
        mid = case.ev.mandate.mandate_id
        self.filed.add(mid)
        comp = late_days * TAT_COMPENSATION_PAISE_PER_DAY
        api.schedule(Action(
            when=When(day, "1430"),
            type=ActionType.FILE_TAT_CLAIM,
            mandate_id=mid,
            reason=(f"debit not reversed within T+{TAT_REVERSAL_DAYS} (RBI DPSS 629); "
                    f"{late_days}d late{' and counting' if ongoing else ''} — "
                    f"Rs.{comp // 100} compensation due suo motu"),
            rejected=["waiting silently (customer bears the float)",
                      "claiming before observing the statement (precision risk)"],
            checks={"observed_not_predicted": True, "deadline_passed": True},
            meta={
                "late_days": late_days,
                "compensation_paise": comp,
                "ongoing": ongoing,
                "deadline_day": case.ev.fail_day + TAT_REVERSAL_DAYS,
                "channel": "UDIR",
                "beneficiary": "customer",   # not merchant revenue; reported separately
            },
        ))
