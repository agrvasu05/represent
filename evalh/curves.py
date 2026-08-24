"""Train-split calibration: how much does salary-window timing matter?

A probe policy runs ONLY on the train split. For each insufficient-funds
failure it makes one immediate re-presentation and one salary-window
re-presentation (both compliant), then we estimate:

    lift = P(success | salary window) / P(success | arbitrary day)

RePresent receives the scalar and decides wait-vs-retry-now with it.
The held-out split never touches this procedure. This is the entire
"learning" in the system — a measured timing prior, applied by rules —
chosen over RL/bandits deliberately (see DECISIONS.md #3).
"""
from __future__ import annotations

from simlab.calendar import PDN_LEAD_DAYS, When, day_of_month
from simlab.engine import HORIZON_DAYS, Action, Engine, Policy, WorldAPI
from simlab.entities import ActionType, Cause, FailureEvent
from simlab.generator import Portfolio


class _ProbePolicy(Policy):
    name = "probe"

    def on_failure(self, ev: FailureEvent, api: WorldAPI) -> None:
        if ev.cause is not Cause.INSUFFICIENT_FUNDS:
            return
        mid = ev.mandate.mandate_id

        def pdn_retry(day: int, tag: str) -> None:
            day = min(day, HORIZON_DAYS)
            api.schedule(Action(
                when=When(max(day - PDN_LEAD_DAYS, ev.fail_day), "1430"),
                type=ActionType.SEND_PDN, mandate_id=mid, reason="probe pdn",
            ))
            api.schedule(Action(
                when=When(day, "0800"), type=ActionType.RETRY_DEBIT,
                mandate_id=mid, reason=f"probe:{tag}", meta={"probe": tag},
            ))

        pdn_retry(ev.fail_day + PDN_LEAD_DAYS + 1, "base")
        sal = ev.mandate.customer.salary_day
        day = ev.fail_day + PDN_LEAD_DAYS + 2
        while day < HORIZON_DAYS and not (0 <= (day_of_month(day) - sal) % 30 <= 1):
            day += 1
        pdn_retry(day, "salary")


def estimate_salary_lift(portfolio: Portfolio, train: list[FailureEvent], seed: int) -> dict:
    engine = Engine(portfolio, train, seed=seed, policy=_ProbePolicy())
    engine.run()
    stats = {"base": [0, 0], "salary": [0, 0]}   # [successes, attempts]
    for rec in engine.trail:
        tag = rec.meta.get("probe")
        if rec.meta.get("note") == "mandate already closed":
            continue  # earlier probe arm already recovered this mandate
        if tag in stats and rec.action is ActionType.RETRY_DEBIT:
            stats[tag][1] += 1
            if rec.outcome.value == "success":
                stats[tag][0] += 1
    p_base = stats["base"][0] / max(stats["base"][1], 1)
    p_sal = stats["salary"][0] / max(stats["salary"][1], 1)
    return {
        "p_base": round(p_base, 4),
        "p_salary": round(p_sal, 4),
        "lift": round(p_sal / max(p_base, 1e-9), 2),
        "n_train_if": stats["base"][1],
    }
