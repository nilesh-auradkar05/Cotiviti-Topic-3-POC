.PHONY: install test lint demo eval fetch-data clean

# One command per concern. Every phase's definition-of-done is a green `make` target.

install:          ## Install package + dev tooling
	pip install -e ".[dev]" --break-system-packages

test:             ## Run the test suite (the gate for every phase)
	pytest

lint:             ## Static checks
	ruff check policyforge tests

demo:             ## End-to-end: ingest -> extract -> gate -> adjudicate on one code pair
	streamlit run app/main.py

eval:             ## Produce the Track A (ablation) + Track B metrics report
	python -m policyforge.evaluation.run_eval

fetch-data:       ## Download the licensed CMS NCCI Practitioner PTP file (needs network egress to www.cms.gov)
	@echo "Q3 2026 Practitioner PTP edits (4 segments) + Policy Manual:"
	@echo "  https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-procedure-procedure-ptp-edits"
	@echo "  https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-policy-manual"
	@echo "Place extracted files under data/. CPT codes are AMA-licensed; accept the click-through."

clean:
	rm -rf build dist *.egg-info .pytest_cache && find . -name __pycache__ -type d -prune -exec rm -rf {} +
