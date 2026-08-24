# Decision log

The choices I own, separate from code that could be generated. Each entry:
what was decided, what was rejected, and why.

## 1. The failure taxonomy and its calibration

Six causes: `insufficient_funds`, `technical_decline_bank`,
`technical_decline_psp`, `mandate_paused`, `mandate_revoked`,
`limit_breach`. Insufficient funds carries ~65% of the mix — anchored to
NACH return-code data where code 01 is 60–70% of returns. Constants are
tagged `[CITED]` or `[ASSUMED]` in `simlab/generator.py`; assumed ones are
exercised by sensitivity runs, not asserted as facts.
**Rejected:** a finer-grained taxonomy (30+ NPCI codes). The policy's
actions only branch six ways; extra classes would add classifier surface
without changing any decision.

## 2. Where the LLM is — and deliberately is not

LLM (Claude): messy-narration classification and nudge drafting. Both are
unstructured-language problems.
Not LLM: the retry scheduler, the constraint checks, the compensation
arithmetic, the auditor. These are exact rules with zero tolerance for
variance; a model here adds cost and nondeterminism with no information
advantage. `make llm-cache` prints the ablation so "what does the LLM
buy" has a number instead of a claim.
**Rejected:** an LLM agent loop choosing actions ("should I retry now?").
Auditability is the product; sampled decisions can't be audited into a
guarantee.

## 3. Policy = measured prior + deterministic rules, not RL/bandits

The single learned quantity is the salary-window timing lift, estimated on
the train split by a probe policy (`evalh/curves.py`) and applied as a
threshold rule. **Rejected:** reinforcement learning / contextual bandits —
they would overfit the simulator I also wrote, can't be audited, and the
action space is small enough that the interesting structure is in the
constraints, not the policy class.

## 4. Conservative readings of ambiguous rules

NPCI's retry circular caps re-presentations (1+3) and confines execution
to non-peak windows; RBI's e-mandate framework requires 24h pre-debit
notification. Whether EVERY re-presentation needs a fresh notification is
not unambiguous in public text. RePresent takes the conservative reading
(fresh PDN per attempt) and the auditor enforces that reading. If the
lenient reading is correct, we sent extra notifications; the reverse
mistake would be a compliance breach.

## 5. Stopping rules and the priced annoyance cost

Max 2 nudges per case, opt-outs honored immediately, revoked = zero
contact, 30-day case timeout, 12-day outage-hold budget, high-value
unresolved cases -> human queue. The eval reports "nudges sent to
unrecoverable customers" as an explicit false-positive cost. The agent
deliberately quits while money is theoretically recoverable: unbounded
recovery IS the dark pattern the track brief warns about.

## 6. What the simulator can and cannot prove

Razorpay test mode cannot simulate failed mandate debits (docs: debits
only within 3 days of token creation, mocked auth) — so a calibrated
simulator is the only honest instrument, and the claim is RELATIVE:
policy quality vs baselines under identical worlds (common random
numbers), stated assumptions, an oracle bound, 5 seeds. The absolute
rupee figures are properties of the simulated portfolio, not a revenue
forecast. Anyone who wants to attack the numbers should start at
`simlab/generator.py`; every attackable constant is labeled.

## 7. TAT claims: observe, then file — never predict

The RBI DPSS 629 compensation module files a claim only after the bank
statement shows a reversal that is late or absent past deadline+grace.
Filing on prediction would juice the compensation number and tank
precision; the auditor recomputes every claim from ground truth and the
published metric is that precision (1.0 across seeds).
