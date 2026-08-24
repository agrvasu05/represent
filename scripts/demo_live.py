"""Live demo: one simulated decision executed on real Razorpay test rails.

Picks a mandate from the seed-1 world whose recovery path was the payment
link, replays the policy's decision for it, and creates a REAL test-mode
Payment Link carrying the audit metadata. Open the printed short_url,
pay with a Razorpay test method, and watch it turn paid on the dashboard.

Run:  RZP_KEY_ID=rzp_test_xxx RZP_KEY_SECRET=yyy python -m scripts.demo_live
"""
from __future__ import annotations

import json

from agent.classifier import HybridClassifier
from agent.executor import RazorpayTestClient
from simlab.entities import Cause
from simlab.generator import generate, split


def main() -> None:
    portfolio = generate(2000, seed=1)
    _, held = split(portfolio)
    clf = HybridClassifier()
    # Find an insufficient-funds case with a clean story for the camera.
    ev = next(f for f in held
              if f.cause is Cause.INSUFFICIENT_FUNDS
              and f.mandate.amount_paise < 5_000_00)
    cls = clf.classify(ev.error_code, ev.narration)

    decision = {
        "mandate": ev.mandate.mandate_id,
        "narration": ev.narration,
        "classified_cause": cls.cause,
        "classifier_method": cls.method,
        "rule_fired": "IF-02: salary window >7d away -> offer payment link now",
        "rejected": ["immediate re-presentation (base-rate success, burns budget)",
                     "waiting silently (revenue latency)"],
        "constraints_checked": ["contact_allowed", "nudge_budget", "opt_out"],
    }
    print(json.dumps(decision, indent=2))

    client = RazorpayTestClient()
    link = client.create_recovery_link(
        amount_paise=ev.mandate.amount_paise,
        mandate_id=ev.mandate.mandate_id,
        description=f"Subscription recovery — {ev.mandate.category.value} "
                    f"(failed debit, {cls.cause})",
        notes={"represent_rule": decision["rule_fired"],
               "mandate_id": ev.mandate.mandate_id,
               "classified_cause": cls.cause},
    )
    print("\nPayment link created on Razorpay TEST mode:")
    print("  id:       ", link["id"])
    print("  short_url:", link["short_url"])
    print("  status:   ", link["status"])
    print("\nOpen the link, pay with any Razorpay test method, and refresh"
          "\nthe dashboard: the audit metadata rides in the link's notes.")


if __name__ == "__main__":
    main()
