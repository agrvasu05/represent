"""Build the Claude classification cache and report the ablation.

Runs the LLM leg over every code-less narration in the standard worlds and
caches responses (out/llm_cache.json, committed) so that `make eval`
replays them without an API key. Then prints heuristic-vs-LLM held-out
accuracy so the "what does the LLM actually buy" question has a number.

Run:  ANTHROPIC_API_KEY=... python -m scripts.build_llm_cache
"""
from __future__ import annotations

from agent.classifier import UNKNOWN, HybridClassifier
from simlab.generator import generate, split


def main() -> None:
    llm = HybridClassifier(use_llm=True)
    heur = HybridClassifier(use_llm=False)
    rows = {"llm": [0, 0, 0], "heuristic": [0, 0, 0]}  # correct, gated, n

    for seed in range(1, 6):
        portfolio = generate(5000, seed)
        _, held = split(portfolio)
        for f in held:
            if f.error_code:
                continue  # code leg is exact; ablation is about messy text
            for name, clf in (("llm", llm), ("heuristic", heur)):
                cls = clf.classify(None, f.narration)
                rows[name][2] += 1
                if cls.cause == f.cause.value:
                    rows[name][0] += 1
                elif cls.cause == UNKNOWN:
                    rows[name][1] += 1

    print("code-less narrations, held-out, 5 seeds")
    for name, (ok, gated, n) in rows.items():
        print(f"  {name:9s} accuracy {ok / n:.3f}   gated {gated / n:.3f}   n={n}")
    print("cache written to out/llm_cache.json — commit it for reproducibility")


if __name__ == "__main__":
    main()
