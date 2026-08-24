.PHONY: eval quick test demo-live llm-cache clean

# Full evaluation: 5,000 mandates x 5 seeds x 4 policies, independent
# compliance audit, metrics tables + HTML report. Reproduces every number
# in the README. Stdlib only; no API key needed.
eval:
	python3 -m evalh.run

# Smaller/faster variant for development.
quick:
	python3 -m evalh.run --quick

test:
	python3 tests/test_compliance.py

# One simulated decision executed on real Razorpay TEST rails.
# Needs RZP_KEY_ID (rzp_test_...) and RZP_KEY_SECRET in the environment.
demo-live:
	python3 -m scripts.demo_live

# Build the Claude classification cache for messy narrations, then report
# the LLM-vs-heuristic ablation. Needs ANTHROPIC_API_KEY; results are
# cached to out/llm_cache.json so eval stays reproducible without a key.
llm-cache:
	python3 -m scripts.build_llm_cache

clean:
	rm -rf out/*.json out/*.md out/*.html out/*.jsonl __pycache__ */__pycache__
