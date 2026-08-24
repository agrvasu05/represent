"""Generate out/metrics.md and out/report.html from metrics.json payload.

Every number in the published tables comes from here — nothing hand-typed.
The HTML report includes the cumulative-recovery chart with the outage
windows shaded (the failure-case exhibit).
"""
from __future__ import annotations

from pathlib import Path

from simlab.generator import OUTAGES

OUT = Path("out")
NAMES = {"no_retry": "No-retry", "naive_retry": "Naive retry bot",
         "represent": "RePresent", "oracle": "Oracle (upper bound)"}


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _inr(paise: float) -> str:
    return f"Rs.{paise / 100:,.0f}"


def write_reports(p: dict) -> None:
    agg = p["aggregate"]
    cfg = p["config"]
    cls = p["classifier_held_out"]

    # ------------------------------------------------------------ markdown
    lines = [
        "# RePresent — evaluation results",
        "",
        f"Held-out split only · {cfg['n']} mandates/seed x {cfg['seeds']} seeds · "
        f"split {cfg['split']} · common random numbers across policies",
        "",
        "## Headline: recovery vs compliance",
        "",
        "| Policy | Recovery (by value) | Recovery (by count) | Recovered (mean) | Compliance violations | Retries/recovery | Nudges to unrecoverable |",
        "|---|---|---|---|---|---|---|",
    ]
    for k in ("no_retry", "naive_retry", "represent", "oracle"):
        a = agg[k]
        lines.append(
            f"| {NAMES[k]} | {_pct(a['recovery_rate']['mean'])} ± {_pct(a['recovery_rate']['sd'])} "
            f"| {_pct(a['recovery_rate_count']['mean'])} ± {_pct(a['recovery_rate_count']['sd'])} "
            f"| {_inr(a['recovered_paise']['mean'])} "
            f"| {a['violations_total']['mean']:.0f} "
            f"| {a['retries_per_recovery']['mean']:.2f} "
            f"| {a['nudges_to_unrecoverable']['mean']:.0f} |"
        )
    lines += [
        "",
        "## Failure case: correlated bank outage",
        "",
        "| Policy | Outage-cohort recovery | Retries burned into outage windows (seed mean) |",
        "|---|---|---|",
    ]
    for k in ("naive_retry", "represent", "oracle"):
        runs = [r for r in p["runs"] if r["policy"] == k]
        burned = sum(r["outage_retries_burned"] for r in runs) / len(runs)
        lines.append(f"| {NAMES[k]} | {_pct(agg[k]['outage_recovery_rate']['mean'])} | {burned:.0f} |")
    lines += [
        "",
        "## TAT module (RBI DPSS 629 compensation)",
        "",
    ]
    a = agg["represent"]
    runs = [r for r in p["runs"] if r["policy"] == "represent"]
    prec = [r["tat_precision"] for r in runs if r["tat_precision"] is not None]
    lines += [
        f"- Claims filed: {a['tat_claims']['mean']:.0f}/seed · "
        f"claim precision (auditor-recomputed): {sum(prec)/len(prec):.3f}" if prec else "- no claims",
        f"- Compensation identified for customers: {_inr(a['tat_compensation_paise']['mean'])}/seed "
        "(reported separately from merchant revenue — beneficiary is the customer)",
        "",
        "## Classifier (held-out, seed 1)",
        "",
        f"- Hybrid accuracy: {_pct(cls['hybrid_accuracy'])} on {cls['n']} events · "
        f"clean-code coverage {_pct(cls['codes_only_coverage'])} · "
        f"gated to human: {cls['gated_to_human']} ({_pct(cls['gated_rate'])})",
        "",
        "| Class | n | accuracy |",
        "|---|---|---|",
    ]
    for cname, d in cls["per_class"].items():
        lines.append(f"| {cname} | {d['n']} | {d['acc']:.3f} |")
    lines += [
        "",
        "_Escalations to human (RePresent): "
        f"{agg['represent']['escalations']['mean']:.0f}/seed — bounded autonomy, not silent failure._",
        "",
    ]
    (OUT / "metrics.md").write_text("\n".join(lines))

    # ---------------------------------------------------------------- html
    (OUT / "report.html").write_text(_html(p))


def _html(p: dict) -> str:
    # cumulative-recovery chart from seed-1 runs
    runs1 = {r["policy"]: r for r in p["runs"] if r["seed"] == 1}
    W, H, PAD = 860, 360, 46
    days = len(next(iter(runs1.values()))["cumulative_recovery"])
    peak = max(max(r["cumulative_recovery"]) for r in runs1.values()) or 1
    colors = {"no_retry": "#9aa0a6", "naive_retry": "#c0392b",
              "represent": "#b8860b", "oracle": "#2c7a4b"}

    def path(policy: str) -> str:
        pts = []
        for d, v in enumerate(runs1[policy]["cumulative_recovery"]):
            x = PAD + d * (W - 2 * PAD) / (days - 1)
            y = H - PAD - v * (H - 2 * PAD) / peak
            pts.append(f"{x:.1f},{y:.1f}")
        return "M" + " L".join(pts)

    shades = ""
    for _, o0, o1 in OUTAGES:
        x0 = PAD + o0 * (W - 2 * PAD) / (days - 1)
        x1 = PAD + (o1 + 1) * (W - 2 * PAD) / (days - 1)
        shades += (f'<rect x="{x0:.0f}" y="{PAD}" width="{x1 - x0:.0f}" '
                   f'height="{H - 2 * PAD}" fill="#c0392b22"/>')
    curves = "".join(
        f'<path d="{path(k)}" fill="none" stroke="{c}" stroke-width="2.5"/>'
        for k, c in colors.items() if k in runs1)
    legend = "".join(
        f'<tspan fill="{c}">&#9632; {NAMES[k]}  </tspan>' for k, c in colors.items())

    md_tables = (OUT / "metrics.md").read_text()
    import html as _h
    return f"""<!doctype html><meta charset="utf-8">
<title>RePresent — eval report</title>
<style>
 body{{font-family:Georgia,serif;max-width:900px;margin:2rem auto;padding:0 1rem;
      background:#faf8f3;color:#1c1917;line-height:1.55}}
 h1{{font-family:Helvetica,Arial,sans-serif}}
 pre{{background:#f1ede3;padding:1rem;overflow-x:auto;font-size:.8rem}}
 .note{{color:#6b6459;font-size:.9rem}}
</style>
<h1>RePresent — evaluation report</h1>
<p class="note">Generated by <code>make eval</code>. Shaded bands are the injected
bank-outage windows; the naive policy's curve flattens inside them because it
burns its NPCI retry budget into a dead bank.</p>
<svg viewBox="0 0 {W} {H}" role="img" aria-label="Cumulative recovery by policy">
 <rect width="{W}" height="{H}" fill="#ffffff"/>
 {shades}{curves}
 <line x1="{PAD}" y1="{H - PAD}" x2="{W - PAD}" y2="{H - PAD}" stroke="#1c1917"/>
 <line x1="{PAD}" y1="{PAD}" x2="{PAD}" y2="{H - PAD}" stroke="#1c1917"/>
 <text x="{PAD}" y="{H - 14}" font-size="12">day 0</text>
 <text x="{W - PAD - 40}" y="{H - 14}" font-size="12">day {days - 1}</text>
 <text x="{PAD}" y="{PAD - 12}" font-size="12">cumulative recovered (paise)</text>
 <text x="{PAD}" y="{H - 2}" font-size="13">{legend}</text>
</svg>
<pre>{_h.escape(md_tables)}</pre>
"""
