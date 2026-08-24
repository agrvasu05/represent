"""Comparator policies.

- NoRetryPolicy: the floor. Writes everything off.
- NaiveRetryPolicy: the honest strawman — what most retry bots actually do.
  Retries on the next three days at 11:30 (peak window), sends no pre-debit
  notifications, and nudges everyone including revoked/opted-out customers.
  Note it does NOT exceed the retry budget: its violations are the realistic
  ones (windows, notifications, consent), not cartoonish ones.
- OraclePolicy: constructed WITH world latents (recoverability, outage
  windows, responsiveness). It is the compliant upper bound — labelled as
  such everywhere — proving uplift is not an artifact of a weak baseline.
"""
from __future__ import annotations

from simlab.calendar import MAX_RETRIES, PDN_LEAD_DAYS, When, day_of_month
from simlab.engine import HORIZON_DAYS, Action, Policy, WorldAPI
from simlab.entities import ActionType, AuditRecord, Cause, FailureEvent, Outcome


class NoRetryPolicy(Policy):
    name = "no_retry"

    def on_failure(self, ev: FailureEvent, api: WorldAPI) -> None:
        api.schedule(Action(
            when=When(ev.fail_day, "2300"), type=ActionType.WRITE_OFF,
            mandate_id=ev.mandate.mandate_id, reason="no recovery policy",
        ))


class NaiveRetryPolicy(Policy):
    name = "naive_retry"

    def on_failure(self, ev: FailureEvent, api: WorldAPI) -> None:
        mid = ev.mandate.mandate_id
        for i in range(1, MAX_RETRIES + 1):
            api.schedule(Action(
                when=When(min(ev.fail_day + i, HORIZON_DAYS), "1130"),  # peak slot
                type=ActionType.RETRY_DEBIT, mandate_id=mid,
                reason=f"retry {i}/3 next day, standard schedule",
                meta={"attempt": i},
            ))
        for i, offset in enumerate((0, 2), start=1):
            api.schedule(Action(
                when=When(min(ev.fail_day + offset, HORIZON_DAYS), "1900"),
                type=ActionType.SEND_NUDGE, mandate_id=mid,
                reason=f"blast reminder {i}",
                meta={"kind": "blast", "nudge_no": i},
            ))


class OraclePolicy(Policy):
    """Upper bound: sees latent state; still obeys every compliance rule."""

    name = "oracle"

    def on_failure(self, ev: FailureEvent, api: WorldAPI) -> None:
        mid = ev.mandate.mandate_id
        cust = ev.mandate.customer

        def pdn_and_retry(day: int, attempt: int) -> None:
            day = min(day, HORIZON_DAYS - 1)
            api.schedule(Action(
                when=When(max(day - PDN_LEAD_DAYS, ev.fail_day), "1430"),
                type=ActionType.SEND_PDN, mandate_id=mid,
                reason="oracle PDN", meta={"for_retry_day": day},
            ))
            api.schedule(Action(
                when=When(day, "0800"), type=ActionType.RETRY_DEBIT,
                mandate_id=mid, reason=f"oracle retry {attempt}",
                meta={"attempt": attempt},
            ))

        if not cust.recoverable or ev.cause in (Cause.MANDATE_REVOKED,):
            api.schedule(Action(
                when=When(ev.fail_day, "2300"), type=ActionType.WRITE_OFF,
                mandate_id=mid, reason="oracle: unrecoverable/revoked",
            ))
            return

        def in_outage(day: int) -> bool:
            return any(ev.mandate.bank == ob and o0 <= day <= o1
                       for ob, o0, o1 in api._e.portfolio.outages)

        if ev.cause is Cause.INSUFFICIENT_FUNDS:
            # Retry inside up to three successive salary windows, skipping
            # outage days at this mandate's bank (oracle knows the calendar).
            day = ev.fail_day + PDN_LEAD_DAYS
            hits = 0
            attempt = 0
            while day < HORIZON_DAYS and hits < MAX_RETRIES:
                if not in_outage(day) and 0 <= (day_of_month(day) - cust.salary_day) % 30 <= 1:
                    attempt += 1
                    pdn_and_retry(day, attempt)
                    hits += 1
                    day += 25
                else:
                    day += 1
            # Oracle uses the payment-link channel too — it KNOWS which
            # customers respond to nudges, so it messages exactly those.
            if cust.responsiveness > 0.35:
                api.schedule(Action(
                    when=When(ev.fail_day, "1430"), type=ActionType.SEND_NUDGE,
                    mandate_id=mid, reason="oracle pay-link to known-responsive customer",
                    meta={"kind": "pay_link", "nudge_no": 1},
                ))
        elif ev.cause in (Cause.TECHNICAL_DECLINE_BANK, Cause.TECHNICAL_DECLINE_PSP):
            day = ev.fail_day + PDN_LEAD_DAYS
            # Oracle KNOWS the outage calendar; first retry lands after it.
            while in_outage(day):
                day += 1
            for attempt in (1, 2):
                pdn_and_retry(day, attempt)
                day += 3
                while in_outage(day):
                    day += 1
        elif ev.cause is Cause.MANDATE_PAUSED:
            for i, off in enumerate((0, 4), start=1):
                api.schedule(Action(
                    when=When(min(ev.fail_day + off, HORIZON_DAYS), "1430"),
                    type=ActionType.SEND_NUDGE, mandate_id=mid,
                    reason=f"oracle resume ask {i}", meta={"kind": "resume", "nudge_no": i},
                ))
        elif ev.cause is Cause.LIMIT_BREACH:
            for i, off in enumerate((0, 4), start=1):
                api.schedule(Action(
                    when=When(min(ev.fail_day + off, HORIZON_DAYS), "1430"),
                    type=ActionType.SEND_NUDGE, mandate_id=mid,
                    reason=f"oracle AFA link {i}", meta={"kind": "afa_link", "nudge_no": i},
                ))

    def on_result(self, rec: AuditRecord, api: WorldAPI) -> None:
        # Post-resume retry for paused mandates.
        if rec.action is ActionType.SEND_NUDGE and rec.meta.get("effect") == "mandate_resumed":
            retry_day = min(rec.day + 2, HORIZON_DAYS)
            api.schedule(Action(
                when=When(retry_day - PDN_LEAD_DAYS, "1430"),
                type=ActionType.SEND_PDN, mandate_id=rec.mandate_id,
                reason="oracle PDN post-resume",
                meta={"for_retry_day": retry_day},
            ))
            api.schedule(Action(
                when=When(retry_day, "0800"),
                type=ActionType.RETRY_DEBIT, mandate_id=rec.mandate_id,
                reason="oracle post-resume retry", meta={"attempt": 1},
            ))
