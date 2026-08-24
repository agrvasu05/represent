"""Synthetic mandate-portfolio generator.

Calibration policy: every distributional constant below is either
  [CITED]    anchored to a public source (link in README's data section), or
  [ASSUMED]  a stated assumption, exercised by the sensitivity suite.
Nothing is tuned against the policy under test; the generator is frozen
before policy work and shared by every policy via common random numbers.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from .calendar import AFA_LIMIT_PAISE
from .entities import Category, Cause, Customer, FailureEvent, Mandate
from .narrations import make_narration

# ---------------------------------------------------------------- constants

# [CITED] NACH return-code 01 (insufficient funds) is 60-70% of returns
# (Terra Insight NACH analysis); remaining mass split across technical,
# pause/revoke and limit causes per NPCI decline taxonomy.
CAUSE_MIX: list[tuple[Cause, float]] = [
    (Cause.INSUFFICIENT_FUNDS, 0.65),
    (Cause.TECHNICAL_DECLINE_BANK, 0.12),
    (Cause.TECHNICAL_DECLINE_PSP, 0.05),
    (Cause.MANDATE_PAUSED, 0.08),
    (Cause.MANDATE_REVOKED, 0.06),
    (Cause.LIMIT_BREACH, 0.04),
]

# [ASSUMED] share of hard-unrecoverable customers among failed mandates.
UNRECOVERABLE_RATE = 0.25

# [CITED-adjacent] Indian salary credits cluster at month boundaries
# (1st dominant, 7th/15th/30th secondary). [ASSUMED] exact weights.
SALARY_DAYS: list[tuple[int, float]] = [(1, 0.50), (7, 0.15), (15, 0.20), (30, 0.15)]

# [ASSUMED] subscription ticket sizes: bulk Rs.99-Rs.4,999 (log-uniform),
# with a 2% slice above the Rs.15,000 AFA ceiling to exercise that branch.
# Kept small so big tickets exercise the AFA path without letting a handful
# of mandates dominate value-weighted metrics.
ABOVE_AFA_SHARE = 0.02

CATEGORIES: list[tuple[Category, float]] = [
    (Category.STREAMING, 0.35),
    (Category.SAAS, 0.25),
    (Category.EDTECH, 0.15),
    (Category.INSURANCE, 0.15),
    (Category.LENDING, 0.10),
]

N_BANKS = 10
# [ASSUMED] concentration: top banks carry more mandates (Zipf-ish weights).
BANK_WEIGHTS = [1.0 / (i + 1) ** 0.6 for i in range(N_BANKS)]

# TAT sub-case [CITED mechanism / ASSUMED rates]: among bank-side technical
# declines, some debited the customer without completing. RBI TAT says
# reversal within T+5, else Rs.100/day. We seed 40% debited-not-reversed;
# of those, 55% reverse on time, 45% late by 1-20 days.
DEBITED_SHARE_OF_TECH_BANK = 0.40
LATE_REVERSAL_SHARE = 0.45

HORIZON_DAYS = 60          # world length
FAILURE_WINDOW_DAYS = 25   # initial failures all land in days 0..24

# Two correlated bank incidents [CITED precedent: NPCI Apr-2025 FY-end
# incident; ASSUMED placement/length]. (bank, start_day, end_day inclusive)
# BANK02 is a prolonged 5-day mandate-execution degradation (backlogged
# processing), BANK06 a short 2-day outage. The sensitivity suite also
# runs a no-outage scenario; with only short outages the naive policy's
# 3-day retry spread partially escapes and the gap narrows — reported,
# not hidden.
OUTAGES: list[tuple[str, int, int]] = [
    ("BANK02", 12, 16),
    ("BANK06", 20, 21),
]


def _weighted(rng: random.Random, pairs):
    r = rng.random()
    acc = 0.0
    for value, w in pairs:
        acc += w
        if r <= acc:
            return value
    return pairs[-1][0]


@dataclass
class Portfolio:
    mandates: list[Mandate]
    failures: list[FailureEvent]
    outages: list[tuple[str, int, int]]


def generate(n: int, seed: int) -> Portfolio:
    rng = random.Random(f"gen-{seed}")
    mandates: list[Mandate] = []
    failures: list[FailureEvent] = []

    for i in range(n):
        cid = f"C{seed:02d}{i:05d}"
        salary_day = _weighted(rng, SALARY_DAYS)
        customer = Customer(
            customer_id=cid,
            salary_day=salary_day,
            recoverable=rng.random() > UNRECOVERABLE_RATE,
            responsiveness=min(1.0, max(0.0, rng.betavariate(2.2, 2.8))),
            opt_out_prone=rng.random() < 0.06,  # [ASSUMED]
        )
        if rng.random() < ABOVE_AFA_SHARE:
            amount = rng.randint(AFA_LIMIT_PAISE + 5_00, 20_000_00)
        else:
            # log-uniform Rs.99..Rs.4999
            import math
            lo, hi = math.log(99_00), math.log(4_999_00)
            amount = int(math.exp(rng.uniform(lo, hi)))
        bank = f"BANK{_weighted(rng, [(b, w / sum(BANK_WEIGHTS)) for b, w in enumerate(BANK_WEIGHTS)]):02d}"
        due_day = rng.randrange(0, FAILURE_WINDOW_DAYS)
        mandate = Mandate(
            mandate_id=f"M{seed:02d}{i:05d}",
            customer=customer,
            amount_paise=amount,
            category=_weighted(rng, CATEGORIES),
            bank=bank,
            due_day=due_day,
            umn=f"UMN{rng.randrange(10**11):011d}",
        )
        mandates.append(mandate)

        cause = _weighted(rng, CAUSE_MIX)
        # Amounts above the AFA ceiling fail as limit breaches by rule.
        if mandate.amount_paise > AFA_LIMIT_PAISE:
            cause = Cause.LIMIT_BREACH
        # Failures during an outage window at an affected bank are
        # bank-technical regardless of the sampled cause.
        for obank, o0, o1 in OUTAGES:
            if bank == obank and o0 <= due_day <= o1:
                cause = Cause.TECHNICAL_DECLINE_BANK
        code, narration = make_narration(cause, rng)

        debited = cause is Cause.TECHNICAL_DECLINE_BANK and rng.random() < DEBITED_SHARE_OF_TECH_BANK
        reversal_day = None
        if debited:
            if rng.random() < LATE_REVERSAL_SHARE:
                reversal_day = due_day + 5 + rng.randint(1, 20)   # late
            else:
                reversal_day = due_day + rng.randint(1, 5)        # on time
        failures.append(
            FailureEvent(
                mandate=mandate,
                fail_day=due_day,
                cause=cause,
                error_code=code,
                narration=narration,
                debited_not_reversed=debited,
                actual_reversal_day=reversal_day,
            )
        )

    return Portfolio(mandates=mandates, failures=failures, outages=list(OUTAGES))


def split(portfolio: Portfolio, train_frac: float = 0.6):
    """Deterministic train/held-out split by mandate-id hash."""
    import hashlib

    train, held = [], []
    for f in portfolio.failures:
        h = int(hashlib.sha256(f.mandate.mandate_id.encode()).hexdigest(), 16)
        (train if (h % 1000) < train_frac * 1000 else held).append(f)
    return train, held
