.DEFAULT_GOAL := help
PY ?= python3

.PHONY: help world scenarios estimate smoke corpus check check-coverage check-permissions check-agent seed-traces check-site site clean trace-up trace-demo trace-down trace-nuke trace-logs

COMPOSE ?= docker compose -f infra/docker-compose.yml
LANGFUSE_URL ?= http://localhost:3000

help:
	@echo "Wayfarer AI Evals Course"
	@echo ""
	@echo "  make world       rebuild the world database from facts.yaml (no API key)"
	@echo "  make scenarios   regenerate the 650 scenarios (no API key)"
	@echo "  make check       run every correctness gate (no API key)"
	@echo "  make estimate    forecast what a corpus run would cost (no API key)"
	@echo "  make smoke       10 scenarios against a real model (needs a key, cents)"
	@echo "  make corpus      the full corpus (needs a key, SPENDS MONEY)"
	@echo "  make site        serve docs/ at http://localhost:8000"
	@echo "  make clean       remove generated artifacts"
	@echo ""
	@echo "  L2 tracing backend (needs Docker, no API key, costs nothing)"
	@echo "  make trace-up    start Langfuse and wait until it is ready"
	@echo "  make trace-demo  run the agent and send five real traces to it"
	@echo "  make trace-down  stop it, keep the data"
	@echo "  make trace-nuke  stop it and delete the volumes"
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

# The only targets that cost money. estimate and smoke exist so that nobody's
# first paid command is the expensive one: a prompt bug found on scenario 3
# costs a cent, and the same bug found on scenario 650 costs the whole run.
estimate:
	$(PY) scripts/run_corpus.py --estimate

smoke:
	$(PY) scripts/run_corpus.py --limit 10 --max-usd 0.50

corpus:
	@echo "This spends real money. Ctrl-C now if you have not run 'make estimate' and 'make smoke'."
	@echo ""
	$(PY) scripts/run_corpus.py --max-usd 10.00

scenarios:
	$(PY) scripts/build_scenarios.py

# Every gate that runs without an API key. Wired into CI.
check: world check-coverage check-permissions scenarios check-agent seed-traces check-site
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
	@echo ""
	@echo "=== lab corpus: is every failure mode still reachable? ==="
	@$(PY) scripts/build_lab_corpus.py
	@echo ""
	@echo "=== red team: are all the attacks still refused? ==="
	@$(PY) scripts/build_redteam.py
	@echo ""
	@echo "=== lab data: coverage grid and cost breakdown ==="
	@$(PY) scripts/build_lab_data.py

check-agent:
	@echo ""
	@echo "=== agent: loop, permission gate, and trace shape ==="
	@$(PY) agent/support_agent.py --selftest

# Structural only. Confirms the site is wired up: links resolve, tags balance,
# no fetch (which would break file://), no external loads. Says nothing about
# whether the lab JavaScript behaves, which needs a browser.
check-site:
	@echo ""
	@echo "=== site: do the links resolve and the tags balance? ==="
	@$(PY) scripts/check_site.py

check-coverage:
	@echo ""
	@echo "=== coverage: is every failure mode reachable? ==="
	@$(PY) scripts/check_coverage.py

check-permissions:
	@echo ""
	@echo "=== drift: do rules.py and permissions.py agree? ==="
	@$(PY) scripts/check_permissions.py

# ---------------------------------------------------------------------------
# L2: the tracing backend
#
# Not part of `make check`. The gates must keep running on a machine with no
# Docker, and a correctness suite that depends on six containers being healthy
# is a suite people stop running.
# ---------------------------------------------------------------------------

trace-up:
	@command -v docker >/dev/null 2>&1 || { echo "docker not found. Install Docker Desktop, or skip this lesson's backend; the trace model in agent/tracing.py is the part that matters."; exit 1; }
	@docker info >/dev/null 2>&1 || { echo "docker is installed but not running. Start Docker Desktop and try again."; exit 1; }
	$(COMPOSE) up -d
	@echo ""
	@echo "waiting for langfuse to finish migrating (first run takes a minute)"
	@for i in $$(seq 1 90); do \
		if curl -fsS $(LANGFUSE_URL)/api/public/health >/dev/null 2>&1; then \
			echo ""; echo "langfuse is up at $(LANGFUSE_URL)"; \
			echo "  sign in with  evals@wayfarer.local / wayfarer-local-dev"; \
			echo "  then:  make trace-demo"; \
			exit 0; \
		fi; \
		printf "."; sleep 2; \
	done; \
	echo ""; echo "timed out. check: make trace-logs"; exit 1

# The demo costs nothing. The selftest drives the real control loop, the real
# permission layer and the real database with a scripted client, so the traces
# are genuine even though no model was called. That includes a denial and a
# step-budget exhaustion, which are the two most instructive things to look at
# in a trace viewer and the two most annoying to reproduce on demand.
trace-demo:
	@curl -fsS $(LANGFUSE_URL)/api/public/health >/dev/null 2>&1 || { echo "langfuse is not responding at $(LANGFUSE_URL). Run: make trace-up"; exit 1; }
	@$(PY) -c "import langfuse" 2>/dev/null || { echo "the langfuse client is not installed. In a virtualenv: pip install -r requirements-tracing.txt"; exit 1; }
	LANGFUSE_TRACING=1 $(PY) agent/support_agent.py --selftest

trace-down:
	$(COMPOSE) down

trace-nuke:
	$(COMPOSE) down -v

trace-logs:
	$(COMPOSE) logs --tail=80 langfuse-web langfuse-worker

clean:
	rm -f data/world/wayfarer.db data/world/world_stats.json
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
