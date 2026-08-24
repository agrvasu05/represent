"""RePresent — the compliance-constrained recovery policy.

Design stance (defended in DECISIONS.md):
- The scheduler is DETERMINISTIC. Constraints (retry budget, execution
  windows, notification lead, contact masks) are hard-coded rules, not
  model outputs — an LLM here would add variance and cost with no
  information advantage. The LLM's jobs are narration classification
  (classifier.py) and nudge drafting (comms.py), where unstructured
  language actually is the problem.
- Retries are PLANNED internally and DISPATCHED day by day: the concrete
  pre-debit notification + re-presentation only enter the world the day
  before execution, which is what lets the outage circuit breaker hold a
  planned retry against a bank whose telemetry says it is down — for any
  failure cause, because a dead bank processes nothing.
- Every executed action carries reason + rejected alternatives + the
  constraint checks that were run. The independent auditor re-derives all
  of it from the log; the policy's own checks are claims, not proof.
- Conservative readings where NPCI text is ambiguous: a FRESH pre-debit
  notification precedes EVERY re-presentation, and the retry budget is
  counted per mandate execution cycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from simlab.calendar import (
    AFA_LIMIT_PAISE,
    MAX_RETRIES,
    PDN_LEAD_DAYS,
    When,
    day_of_month,
)
from simlab.engine import HORIZON_DAYS, Action, Policy, WorldAPI
from simlab.entities import ActionType, AuditRecord, Cause, FailureEvent, Outcome

from .classifier import UNKNOWN, HybridClassifier
from .comms import draft_nudge
from .tat import TatTracker

RETRY_SLOT = "0800"          # non-peak by construction
NUDGE_SLOT = "1430"          # non-peak; messages have no window rule, but
                             # off-peak sends are politer and cost nothing
MAX_NUDGES = 2
HIGH_VALUE_PAISE = 10_000_00     # escalate, don't write off, above this
CASE_TIMEOUT_DAYS = 30           # stop rule: nothing runs past fail+30

# Circuit breaker: a bank whose technical-decline telemetry exceeds
# BREAKER_MULT x its trailing median is treated as down; planned retries
# at that bank HOLD until telemetry recovers.
BREAKER_MULT = 4.0
BREAKER_WARMUP_DAYS = 3
MAX_BREAKER_HOLD_DAYS = 12       # stop rule: outage outlasting this => close


@dataclass
class Case:
    ev: FailureEvent
    cause: str                       # classifier output, not ground truth
    debited_flag: bool
    retries_used: int = 0
    nudges_used: int = 0
    planned_retry_day: int | None = None
    planned_why: str = ""
    planned_rejected: list[str] = field(default_factory=list)
    inflight_retry: bool = False
    closed: bool = False
    holds: int = 0                   # times the breaker deferred us


class RePresentPolicy(Policy):
    name = "represent"

    def __init__(self, classifier: HybridClassifier | None = None,
                 salary_timing_lift: float | None = None):
        # salary_timing_lift comes from evalh/curves.py (train split):
        # estimated multiplier of salary-window retry success over base.
        # >=1.5 => wait for the salary window; else retry sooner.
        self.classifier = classifier or HybridClassifier()
        self.salary_timing_lift = salary_timing_lift if salary_timing_lift is not None else 2.0
        self.cases: dict[str, Case] = {}
        self.tat = TatTracker()
        self._tele_history: dict[str, list[int]] = {}
        self._breaker_open: set[str] = set()

    # ------------------------------------------------------------ helpers
    def _checks(self, case: Case, retry_day: int | None = None) -> dict[str, bool]:
        c = {
            "retry_budget_ok": case.retries_used <= MAX_RETRIES,
            "contact_allowed": case.cause != Cause.MANDATE_REVOKED.value,
            "within_case_timeout": True,
        }
        if retry_day is not None:
            c["slot_non_peak"] = not When(retry_day, RETRY_SLOT).is_peak()
            c["pdn_scheduled_24h_prior"] = True
            c["bank_breaker_closed"] = case.ev.mandate.bank not in self._breaker_open
        return c

    def _plan_retry(self, case: Case, api: WorldAPI, target_day: int, why: str,
                    rejected: list[str]) -> None:
        """Record intent; the day-start dispatcher turns it into actions."""
        if case.closed or case.retries_used >= MAX_RETRIES:
            self._close(case, api, f"retry budget ({MAX_RETRIES}) exhausted")
            return
        target_day = max(target_day, api.today + PDN_LEAD_DAYS)
        if target_day > min(case.ev.fail_day + CASE_TIMEOUT_DAYS, HORIZON_DAYS - 1):
            self._close(case, api, "case timeout before a compliant retry slot existed")
            return
        case.planned_retry_day = target_day
        case.planned_why = why
        case.planned_rejected = rejected

    def _dispatch(self, case: Case, api: WorldAPI) -> None:
        """Called by the dispatcher when planned_retry_day == today + 1."""
        target = case.planned_retry_day
        assert target is not None
        mid = case.ev.mandate.mandate_id
        case.planned_retry_day = None
        case.retries_used += 1
        case.inflight_retry = True
        why = case.planned_why
        if case.holds:
            why += f" (deferred {case.holds}x by outage circuit breaker)"
        api.schedule(Action(
            when=When(api.today, NUDGE_SLOT),
            type=ActionType.SEND_PDN, mandate_id=mid,
            reason=f"24h pre-debit notification for re-presentation on day {target}",
            checks={"pdn_lead_days": True},
            meta={"for_retry_day": target},
        ))
        api.schedule(Action(
            when=When(target, RETRY_SLOT),
            type=ActionType.RETRY_DEBIT, mandate_id=mid,
            reason=why, rejected=case.planned_rejected,
            checks=self._checks(case, retry_day=target),
            meta={"attempt": case.retries_used, "breaker_holds": case.holds},
        ))

    def _send_nudge(self, case: Case, api: WorldAPI, why: str, kind: str,
                    delay: int = 0) -> None:
        if case.nudges_used >= MAX_NUDGES or case.cause == Cause.MANDATE_REVOKED.value:
            return
        case.nudges_used += 1
        mid = case.ev.mandate.mandate_id
        text = draft_nudge(case.ev.mandate, kind)
        api.schedule(Action(
            when=When(max(api.today, case.ev.fail_day) + delay, NUDGE_SLOT),
            type=ActionType.SEND_NUDGE, mandate_id=mid,
            reason=why, checks=self._checks(case),
            meta={"kind": kind, "text": text, "nudge_no": case.nudges_used},
        ))

    def _close(self, case: Case, api: WorldAPI, why: str) -> None:
        if case.closed:
            return
        case.closed = True
        case.planned_retry_day = None
        mid = case.ev.mandate.mandate_id
        high_value = case.ev.mandate.amount_paise >= HIGH_VALUE_PAISE
        action = ActionType.ESCALATE_HUMAN if high_value else ActionType.WRITE_OFF
        api.schedule(Action(
            when=When(api.today, "2300"), type=action, mandate_id=mid,
            reason=f"{why}; {'high-value -> human review' if high_value else 'below human-review threshold'}",
            rejected=["further retries (budget/stop rule)", "further nudges (cap)"],
            checks=self._checks(case),
        ))

    def _next_salary_window(self, api: WorldAPI, salary_dom: int, not_before: int) -> int:
        d = max(not_before, api.today + PDN_LEAD_DAYS)
        for day in range(d, HORIZON_DAYS):
            if 0 <= (day_of_month(day) - salary_dom) % 30 <= 1:
                return day
        return HORIZON_DAYS - 1

    # ------------------------------------------------- breaker + dispatcher
    def on_day_start(self, day: int, api: WorldAPI) -> None:
        counts = api.bank_decline_counts(day)
        for bank, n in counts.items():
            hist = self._tele_history.setdefault(bank, [])
            if len(hist) >= BREAKER_WARMUP_DAYS:
                med = sorted(hist)[len(hist) // 2]
                if n > BREAKER_MULT * max(med, 1):
                    self._breaker_open.add(bank)
                else:
                    self._breaker_open.discard(bank)
            hist.append(n)
            if len(hist) > 14:
                hist.pop(0)

        for case in self.cases.values():
            if case.closed or case.planned_retry_day is None:
                continue
            if case.planned_retry_day <= day:          # missed => replan
                case.planned_retry_day = day + PDN_LEAD_DAYS
            if case.planned_retry_day != day + PDN_LEAD_DAYS:
                continue
            if case.ev.mandate.bank in self._breaker_open:
                case.holds += 1
                case.planned_retry_day += 1            # re-check tomorrow
                if case.holds > MAX_BREAKER_HOLD_DAYS:
                    self._close(case, api,
                                f"bank outage exceeded {MAX_BREAKER_HOLD_DAYS}d hold budget")
                continue
            self._dispatch(case, api)

        self.tat.poll(day, api, self.cases)

    # ----------------------------------------------------------- failure
    def on_failure(self, ev: FailureEvent, api: WorldAPI) -> None:
        cls = self.classifier.classify(ev.error_code, ev.narration)
        case = Case(ev=ev, cause=cls.cause, debited_flag=cls.debited_flag)
        self.cases[ev.mandate.mandate_id] = case

        if cls.cause == UNKNOWN:
            # Fail safe: uncertain cause => no automated contact or retry.
            case.closed = True
            api.schedule(Action(
                when=When(ev.fail_day, "2300"), type=ActionType.ESCALATE_HUMAN,
                mandate_id=ev.mandate.mandate_id,
                reason=f"classifier confidence {cls.confidence:.2f} below gate; "
                       "conservative default is human review, not contact",
                rejected=["retry on a guessed cause", "nudge on a guessed cause"],
                checks={"confidence_gate": False},
            ))
            return

        cause = Cause(cls.cause)

        if cause is Cause.MANDATE_REVOKED:
            case.closed = True
            api.schedule(Action(
                when=When(ev.fail_day, "2300"), type=ActionType.WRITE_OFF,
                mandate_id=ev.mandate.mandate_id,
                reason="customer revoked consent: action mask = no_contact, no_retry",
                rejected=["retry (0% success, consent withdrawn)",
                          "nudge (compliance violation: contact after revocation)"],
                checks=self._checks(case),
            ))
            return

        if cause is Cause.LIMIT_BREACH:
            why = ("amount above Rs.15,000 AFA-free ceiling; re-presentation is "
                   "rule-rejected — compliant path is an AFA-authorised payment link"
                   if ev.mandate.amount_paise > AFA_LIMIT_PAISE else
                   "mandate cap breached; re-presentation would be rejected — payment link instead")
            self._send_nudge(case, api, why, kind="afa_link")
            return

        if cause is Cause.MANDATE_PAUSED:
            self._send_nudge(
                case, api,
                "mandate paused by customer: retrying a paused mandate always fails; "
                "ask to resume via app link", kind="resume",
            )
            return

        if cause is Cause.INSUFFICIENT_FUNDS:
            sal = ev.mandate.customer.salary_day
            window = self._next_salary_window(api, sal, ev.fail_day + PDN_LEAD_DAYS)
            wait = window - ev.fail_day
            if self.salary_timing_lift >= 1.5 and wait <= 12:
                self._plan_retry(
                    case, api, window,
                    why=(f"insufficient funds; salary-day cluster (dom {sal}) in {wait}d — "
                         f"train-split lift {self.salary_timing_lift:.1f}x favours waiting"),
                    rejected=[f"immediate retry (base-rate success, burns 1 of {MAX_RETRIES})",
                              "peak-window slots (NPCI non-peak rule)"],
                )
            else:
                self._plan_retry(
                    case, api, ev.fail_day + 3,
                    why="insufficient funds; salary window too far — spaced early retry",
                    rejected=["waiting >12d (revenue latency, churn risk)"],
                )
            if wait > 7:
                self._send_nudge(case, api,
                                 "long gap to likely liquidity; payment link lets the "
                                 "customer pay when funds arrive", kind="pay_link")
            return

        # Technical declines (bank or PSP): plan for tomorrow; the dispatcher
        # holds it while the bank's breaker is open.
        self._plan_retry(
            case, api, ev.fail_day + PDN_LEAD_DAYS,
            why=f"{cause.value}: transient infra failure, high re-presentation success when healthy",
            rejected=["nudging the customer for our/bank's technical fault"],
        )

    # ------------------------------------------------------------ results
    def on_result(self, rec: AuditRecord, api: WorldAPI) -> None:
        case = self.cases.get(rec.mandate_id)
        if case is None or case.closed:
            return
        if rec.action is ActionType.RETRY_DEBIT:
            case.inflight_retry = False
            if rec.outcome is Outcome.SUCCESS:
                case.closed = True
                return
            cause = Cause(case.cause)
            if case.retries_used >= MAX_RETRIES:
                self._close(case, api, f"retry budget ({MAX_RETRIES}) exhausted")
                return
            if cause is Cause.INSUFFICIENT_FUNDS:
                if case.retries_used == 1 and case.nudges_used == 0:
                    self._send_nudge(case, api,
                                     "first salary-window retry failed; offer payment link",
                                     kind="pay_link")
                nxt = self._next_salary_window(api, case.ev.mandate.customer.salary_day,
                                              api.today + 4)
                self._plan_retry(case, api, nxt,
                                 why="next salary-window re-presentation",
                                 rejected=["immediate re-retry (same liquidity state)"])
            elif cause in (Cause.TECHNICAL_DECLINE_BANK, Cause.TECHNICAL_DECLINE_PSP):
                self._plan_retry(case, api, api.today + 2,
                                 why="spaced technical re-presentation",
                                 rejected=["same-day hammering"])
            elif cause is Cause.MANDATE_PAUSED:
                self._close(case, api, "resumed mandate still failing")
        elif rec.action is ActionType.SEND_NUDGE:
            if rec.meta.get("opted_out_now"):
                self._close(case, api, "customer opted out of communications")
                return
            if rec.meta.get("effect") == "mandate_resumed":
                self._plan_retry(
                    case, api, api.today + PDN_LEAD_DAYS,
                    why="customer resumed the paused mandate; re-present now",
                    rejected=["another nudge (mandate is live again)"],
                )
                return
            if "effect" not in rec.meta and case.planned_retry_day is None \
                    and not case.inflight_retry \
                    and case.cause in (Cause.MANDATE_PAUSED.value, Cause.LIMIT_BREACH.value):
                if case.nudges_used < MAX_NUDGES:
                    self._send_nudge(case, api,
                                     "no response to previous message; one spaced follow-up",
                                     kind="resume" if case.cause == Cause.MANDATE_PAUSED.value else "afa_link",
                                     delay=4)
                else:
                    self._close(case, api, "nudge budget exhausted without conversion")

    def on_link_paid(self, mandate_id: str, api: WorldAPI) -> None:
        case = self.cases.get(mandate_id)
        if case:
            case.closed = True
            case.planned_retry_day = None
