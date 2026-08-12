.DEFAULT_GOAL := help
PY ?= python3

.PHONY: help world scenarios check check-coverage check-permissions clean

help:
	@echo "Wayfarer AI Evals Course"
	@echo ""
	@echo "  make world       rebuild the world database from facts.yaml (no API key)"
	@echo "  make scenarios   regenerate the 650 scenarios (no API key)"
	@echo "  make check       run every correctness gate (no API key)"
	@echo "  make clean       remove generated artifacts"
	@echo ""
	@echo "Track A needs none of this. Open docs/index.html in a browser."

world:
	$(PY) data/world/build_world.py

scenarios:
	$(PY) scripts/build_scenarios.py

# Every gate that runs without an API key. Wired into CI.
check: world check-coverage check-permissions scenarios
	@echo ""
	@echo "all gates passed"

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
