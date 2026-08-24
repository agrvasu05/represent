"""The tests a reviewer should read first.

The load-bearing property: under ANY narration the generator can produce,
a revoked mandate is never classified as something contactable — it comes
back either `mandate_revoked` (mask applies) or `unknown` (gate applies,
-> human, no contact). Both paths forbid contact, so the R4 violation
count of zero is a property, not luck.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.classifier import UNKNOWN, HybridClassifier
from agent.policy import RePresentPolicy
from auditor.validate import audit
from simlab.calendar import When, add_working_days, is_working_day
from simlab.engine import Engine
from simlab.entities import ActionType, AuditRecord, Cause, Outcome
from simlab.generator import generate, split
from simlab.narrations import make_narration


def test_revoked_never_contactable():
    clf = HybridClassifier(use_llm=False)
    rng = random.Random(7)
    for _ in range(500):
        code, narration = make_narration(Cause.MANDATE_REVOKED, rng)
        cls = clf.classify(code, narration)
        assert cls.cause in (Cause.MANDATE_REVOKED.value, UNKNOWN), narration


def test_represent_zero_violations_end_to_end():
    portfolio = generate(600, seed=99)
    _, held = split(portfolio)
    eng = Engine(portfolio, held, seed=99, policy=RePresentPolicy(
        classifier=HybridClassifier(use_llm=False), salary_timing_lift=2.0))
    eng.run()
    report = audit(eng.trail, held)
    assert report.total_violations == 0, report.counts


def test_auditor_catches_planted_violations():
    portfolio = generate(50, seed=5)
    _, held = split(portfolio)
    ev = held[0]
    mid = ev.mandate.mandate_id
    trail = [
        # 4 retries (budget breach), one in a peak slot, none with a PDN
        AuditRecord(seq=i + 1, policy="planted", mandate_id=mid,
                    day=ev.fail_day + 1 + i, slot="1130" if i == 0 else "0800",
                    action=ActionType.RETRY_DEBIT, reason="x", rejected=[],
                    checks={}, outcome=Outcome.FAILED)
        for i in range(4)
    ]
    rep = audit(trail, held)
    assert rep.counts["R1_retry_budget"] == 1
    assert rep.counts["R2_peak_window"] == 1
    assert rep.counts["R3_pdn_missing"] == 4


def test_working_day_calendar():
    # Day 0 is a Monday; day 5 is Saturday #0 (working), day 12 is
    # Saturday #1 (2nd Saturday -> non-working), day 6/13 are Sundays.
    assert is_working_day(5)
    assert not is_working_day(12)
    assert not is_working_day(6)
    assert add_working_days(11, 1) == 14  # skips 2nd Sat + Sunday


def test_peak_slots():
    assert When(3, "1130").is_peak() and When(3, "1900").is_peak()
    assert not When(3, "0800").is_peak()


def test_common_random_numbers_reproducible():
    from evalh.run import run_policy
    from agent.baselines import NaiveRetryPolicy
    portfolio = generate(300, seed=3)
    _, held = split(portfolio)
    m1, _ = run_policy(portfolio, held, 3, NaiveRetryPolicy())
    m2, _ = run_policy(portfolio, held, 3, NaiveRetryPolicy())
    assert m1["recovered_paise"] == m2["recovered_paise"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("all tests passed")
