"""Evaluation harness.

For each seed: generate a portfolio, split train/held-out, calibrate the
salary-timing prior on train, run every policy on the SAME held-out set
under common random numbers, audit every trail independently, and emit
metrics. Aggregates are mean +/- sd across seeds. Nothing in this file is
hand-typed into the README — the tables are generated from here.

Usage:  python -m evalh.run [--n 5000] [--seeds 5] [--quick]
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

from agent.baselines import NaiveRetryPolicy, NoRetryPolicy, OraclePolicy
from agent.classifier import HybridClassifier
from agent.policy import RePresentPolicy
from auditor.validate import audit
from simlab.engine import Engine, HORIZON_DAYS
from simlab.entities import ActionType, Cause, Outcome
from simlab.generator import OUTAGES, generate, split

from .curves import estimate_salary_lift

OUT = Path("out")


def run_policy(portfolio, held, seed, policy):
    eng = Engine(portfolio, held, seed=seed, policy=policy)
    eng.run()
    states = eng.states()
    trail = eng.trail
    rep = audit(trail, held)

    at_risk = sum(f.mandate.amount_paise for f in held)
    recovered = sum(s.failure.mandate.amount_paise for s in states.values()
                    if s.status == "recovered")
    via = {}
    for s in states.values():
        if s.status == "recovered":
            via[s.recovered_via] = via.get(s.recovered_via, 0) + 1
    n_rec = sum(1 for s in states.values() if s.status == "recovered")

    retries = [r for r in trail if r.action is ActionType.RETRY_DEBIT]
    nudges = [r for r in trail if r.action is ActionType.SEND_NUDGE]
    truth = {f.mandate.mandate_id: f for f in held}
    nudges_to_unrecoverable = sum(
        1 for r in nudges
        if not truth[r.mandate_id].mandate.customer.recoverable
    )
    outage_retries = 0
    for r in retries:
        f = truth[r.mandate_id]
        if any(f.mandate.bank == ob and o0 <= r.day <= o1 for ob, o0, o1 in OUTAGES):
            outage_retries += 1

    # Recovery among the outage-affected cohort (the failure-case metric).
    cohort = [f for f in held if any(
        f.mandate.bank == ob and o0 <= f.fail_day <= o1 for ob, o0, o1 in OUTAGES)]
    cohort_rec = sum(1 for f in cohort
                     if states[f.mandate.mandate_id].status == "recovered")

    # Cumulative recovery timeseries (for the report chart).
    daily = [0] * (HORIZON_DAYS + 1)
    for s in states.values():
        if s.status == "recovered" and s.recovered_day is not None:
            daily[s.recovered_day] += s.failure.mandate.amount_paise
    cumulative = []
    acc = 0
    for d in daily:
        acc += d
        cumulative.append(acc)

    tat_claims = [r for r in trail if r.action is ActionType.FILE_TAT_CLAIM]
    tat_comp = sum(r.meta.get("compensation_paise", 0) for r in tat_claims)

    return {
        "policy": policy.name,
        "n_held": len(held),
        "at_risk_paise": at_risk,
        "recovered_paise": recovered,
        "recovery_rate": round(recovered / at_risk, 4),
        "recovery_rate_count": round(n_rec / max(len(held), 1), 4),
        "recovered_count": n_rec,
        "recovered_via": via,
        "retries_total": len(retries),
        "retries_per_recovery": round(len(retries) / max(n_rec, 1), 2),
        "nudges_total": len(nudges),
        "nudges_to_unrecoverable": nudges_to_unrecoverable,
        "escalations": sum(1 for s in states.values() if s.status == "escalated"),
        "violations": rep.counts,
        "violations_total": rep.total_violations,
        "violation_examples": rep.examples,
        "outage_retries_burned": outage_retries,
        "outage_cohort_n": len(cohort),
        "outage_cohort_recovered": cohort_rec,
        "tat_claims": len(tat_claims),
        "tat_claims_valid": rep.tat_claims_valid,
        "tat_precision": rep.tat_precision,
        "tat_compensation_paise": tat_comp,
        "cumulative_recovery": cumulative,
    }, trail


def classifier_accuracy(held, classifier):
    """Held-out classifier accuracy + ablation (codes-only vs hybrid)."""
    total = len(held)
    correct_hybrid = correct_codes = gated = 0
    per_class: dict[str, list[int]] = {}
    for f in held:
        cls = classifier.classify(f.error_code, f.narration)
        truthv = f.cause.value
        pc = per_class.setdefault(truthv, [0, 0])
        pc[1] += 1
        if cls.cause == truthv:
            correct_hybrid += 1
            pc[0] += 1
        elif cls.cause == "unknown":
            gated += 1
        if f.error_code:
            correct_codes += 1  # code map is exact by construction
    return {
        "n": total,
        "hybrid_accuracy": round(correct_hybrid / total, 4),
        "codes_only_coverage": round(correct_codes / total, 4),
        "gated_to_human": gated,
        "gated_rate": round(gated / total, 4),
        "per_class": {k: {"correct": v[0], "n": v[1], "acc": round(v[0] / v[1], 3)}
                      for k, v in sorted(per_class.items())},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--quick", action="store_true", help="n=800, 2 seeds")
    args = ap.parse_args()
    if args.quick:
        args.n, args.seeds = 800, 2

    OUT.mkdir(exist_ok=True)
    all_runs = []
    sample_trail_written = False

    for seed in range(1, args.seeds + 1):
        portfolio = generate(args.n, seed)
        train, held = split(portfolio)
        calib = estimate_salary_lift(portfolio, train, seed)
        classifier = HybridClassifier()

        policies = [
            NoRetryPolicy(),
            NaiveRetryPolicy(),
            RePresentPolicy(classifier=classifier, salary_timing_lift=calib["lift"]),
            OraclePolicy(),
        ]
        for pol in policies:
            metrics, trail = run_policy(portfolio, held, seed, pol)
            metrics["seed"] = seed
            metrics["calibration"] = calib
            all_runs.append(metrics)
            print(f"seed {seed} {pol.name:12s} recovery {metrics['recovery_rate']:.1%} "
                  f"violations {metrics['violations_total']:4d} "
                  f"retries/rec {metrics['retries_per_recovery']}")
            if pol.name == "represent" and not sample_trail_written:
                with open(OUT / "audit_trail_sample.jsonl", "w") as fh:
                    for rec in trail:
                        fh.write(json.dumps({
                            "seq": rec.seq, "mandate": rec.mandate_id,
                            "day": rec.day, "slot": rec.slot,
                            "action": rec.action.value, "reason": rec.reason,
                            "rejected": rec.rejected, "checks": rec.checks,
                            "outcome": rec.outcome.value if rec.outcome else None,
                            "meta": rec.meta,
                        }) + "\n")
                sample_trail_written = True

        if seed == 1:
            cls_metrics = classifier_accuracy(held, classifier)

    # ------------------------------------------------------------ aggregate
    agg: dict[str, dict] = {}
    for name in ("no_retry", "naive_retry", "represent", "oracle"):
        runs = [r for r in all_runs if r["policy"] == name]
        def ms(key):
            vals = [r[key] for r in runs]
            return {"mean": round(st.mean(vals), 4),
                    "sd": round(st.stdev(vals), 4) if len(vals) > 1 else 0.0}
        agg[name] = {
            "recovery_rate": ms("recovery_rate"),
            "recovery_rate_count": ms("recovery_rate_count"),
            "recovered_paise": ms("recovered_paise"),
            "violations_total": ms("violations_total"),
            "retries_per_recovery": ms("retries_per_recovery"),
            "nudges_to_unrecoverable": ms("nudges_to_unrecoverable"),
            "outage_recovery_rate": {
                "mean": round(st.mean([r["outage_cohort_recovered"] / max(r["outage_cohort_n"], 1)
                                       for r in runs]), 4)},
            "escalations": ms("escalations"),
            "tat_claims": ms("tat_claims"),
            "tat_compensation_paise": ms("tat_compensation_paise"),
        }

    payload = {
        "config": {"n": args.n, "seeds": args.seeds, "split": "60/40 by id-hash",
                   "held_out_only": True},
        "aggregate": agg,
        "classifier_held_out": cls_metrics,
        "runs": all_runs,
    }
    (OUT / "metrics.json").write_text(json.dumps(payload, indent=1))
    from .report import write_reports
    write_reports(payload)
    print(f"\nwrote out/metrics.json, out/metrics.md, out/report.html")


if __name__ == "__main__":
    main()
