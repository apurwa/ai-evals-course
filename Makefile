.DEFAULT_GOAL := help
PY ?= python3

.PHONY: help world scenarios check check-coverage check-permissions check-agent seed-traces site clean

help:
	@echo "Wayfarer AI Evals Course"
	@echo ""
	@echo "  make world       rebuild the world database from facts.yaml (no API key)"
	@echo "  make scenarios   regenerate the 650 scenarios (no API key)"
	@echo "  make check       run every correctness gate (no API key)"
	@echo "  make site        serve docs/ at http://localhost:8000"
	@echo "  make clean       remove generated artifacts"
	@echo ""
	@echo "Track A needs none of this. Open docs/index.html in a browser."

# Optional. The labs are written to work from file:// with no server at all,
# which is the point of Track A. This target exists for anyone who prefers a
# real origin, or who wants to check a change the way GitHub Pages will serve it.
site:
	@echo "serving docs/ at http://localhost:8000  (ctrl-c to stop)"
	@cd docs && $(PY) -m http.server 8000

world:
	$(PY) data/world/build_world.py

scenarios:
	$(PY) scripts/build_scenarios.py

# Every gate that runs without an API key. Wired into CI.
check: world check-coverage check-permissions scenarios check-agent seed-traces
	@echo ""
	@echo "all gates passed"

# The three traces the L1 browser lab runs on. This is a gate, not a build
# step: the generator asserts that each case still demonstrates what the lab
# claims it does. If a policy number moves and the "allowed but wrong" case
# starts getting denied, the lab would quietly teach the opposite lesson and
# nothing would look broken. That failure is worth failing CI over.
seed-traces:
	@echo ""
	@echo "=== lab data: do the L1 cases still teach what they claim? ==="
	@$(PY) scripts/build_seed_traces.py

check-agent:
	@echo ""
	@echo "=== agent: loop, permission gate, and trace shape ==="
	@$(PY) agent/support_agent.py --selftest

check-coverage:
	@echo ""
	@echo "=== coverage: is every failure mode reachable? ==="
	@$(PY) scripts/check_coverage.py

check-permissions:
	@echo ""
	@echo "=== drift: do rules.py and permissions.py agree? ==="
	@$(PY) scripts/check_permissions.py

clean:
	rm -f data/world/wayfarer.db data/world/world_stats.json
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
