# AI Evals Course

A hands-on, open-source course on evaluating and improving LLM-powered products.
You build a support agent, instrument it, find its failures with a disciplined
error-analysis process, put it under a regression suite, red-team it, and then
improve both accuracy and cost.

Two ways to take it.

## Status

Built in the open, so here is where it actually stands.

| Part | State |
| --- | --- |
| World, ground-truth rules, permission layer, correctness gates | Built and tested |
| 650 scenarios with computed expected outcomes | Built |
| Support agent, trace schema, agent selftest | Built, runs with no API key |
| **L1 Foundations, lesson and browser lab** | **Written, works** |
| L2 to L10 lesson pages and labs | Outlined, not written |
| Model-generated trace corpus | Needs an API key run |

Anything not written yet is left unlinked rather than linked to a stub.

## Track A: run it in your browser

Nothing to install. No API key. No Docker. No web server.

Open `docs/index.html` and start Lesson 1. Labs load their data from a plain
script file rather than by `fetch`, specifically so that opening the HTML
straight off your disk works and the no-install promise is actually true.

## Track B: run it on your machine

```bash
git clone https://github.com/apurwa/ai-evals-course
cd ai-evals-course

python3 -m venv .venv && source .venv/bin/activate    # do not skip this
pip install -r requirements-core.txt                  # one package

make check     # every correctness gate, no API key needed
```

`make check` should print `all gates passed`. If it does not, stop and read the
output rather than continuing, because everything downstream assumes those
invariants hold.

**Use a virtualenv.** `requirements-core.txt` is one package and is all the
gates need. `requirements.txt` adds the agent runtime and the L5 statistics
libraries. `requirements-tracing.txt` is separate again, because Langfuse pins
`opentelemetry` and `protobuf` tightly enough to upgrade them out from under
everything else sharing the environment. That is exactly what it did the first
time we installed it into a conda base, and breaking someone's unrelated
packages in lesson two is a bad way to teach them about instrumentation.

To run the agent against a live model and generate your own traces you will
need an OpenAI API key. See `docs/lessons/01-foundations.html`.

---

## Why the world is fake, and why that is the point

The agent works for **Wayfarer Supply Co.**, a fictional outdoor gear retailer.
Its customers, products, orders, and shipments are generated deterministically
from `data/world/facts.yaml`.

This is not a shortcut. It is what makes the course teachable:

- **Expected outcomes are computed, never guessed.** `data/world/rules.py`
  derives the correct answer for every scenario from the database and the
  written policy. If a scenario's expected answer cannot be computed, it does
  not belong in the corpus.
- **The world clock is frozen.** `world.now` is fixed at 2026-08-01. Nothing in
  this repository reads the system clock. If it did, every return-window
  computation would drift and the committed corpus would rot within weeks.
- **The database is byte-reproducible.** `make world` produces an identical
  file on any machine, so your results are comparable to everyone else's.

## Correctness gates

The policy is written down in three places, which is how policies drift. Two
scripts make drift impossible to ship quietly.

| Gate | What it catches |
|---|---|
| `scripts/check_coverage.py` | A failure mode the world can never produce. Caught a real one: no delivery was older than nine months, so a twelve-month warranty could never lapse and `warranty_expired` was unreachable. |
| `scripts/check_permissions.py` | `rules.py` and `permissions.py` disagreeing about the same policy. Sweeps every boundary at limit-1, limit, and limit+1. |

Both are release gates, not niceties.

## Authorization is enforced in code

`agent/permissions.py` gates every tool call before its body runs. It never
consults the model.

> A prompt injection that convinces the model to issue a $5000 refund still
> fails, because the check runs in code the model cannot reach or argue with.

Prompt injection has no reliable detector. Guardrails that ask a model to notice
an attack are defense in depth. They are never the boundary. Demonstrating that
gap, live, is what Module 4 is for.

## Layout

```
docs/          Track A. The course site. No build step.
agent/         The support agent, its SPEC.md, tools, and permission layer.
data/world/    facts.yaml, the world generator, and rules.py (ground truth).
evals/         Judges, code checks, splits, harness.
scripts/       Correctness gates.
infra/         Docker compose for Langfuse and ClickHouse.
```

Start with `agent/spec/SPEC.md`. Everything else is downstream of it.

---

## Credits

This course teaches the method taught in **AI Evals for Engineers & PMs** by
[Hamel Husain](https://github.com/hamelsmu) and Shreya Shankar, authors of the
forthcoming O'Reilly book *Evals for AI Engineers*.

This repository is an independent open-source implementation. The agent, the
world, the labs, and all text here are our own. No course or book material is
reproduced. If you want the real thing, take their course and read their book.

Track B installs their companion skills, which are excellent and are used
directly rather than reimplemented:

```
/plugin marketplace add hamelsmu/evals-skills
/plugin install evals-skills@hamelsmu-evals-skills
```

| Lesson | Skill it uses |
|---|---|
| L3 synthetic data | `generate-synthetic-data` |
| L4 error analysis | `error-analysis`, `build-review-interface` |
| L5 evaluators | `write-judge-prompt`, `validate-evaluator` |
| L6, L8 | `eval-audit` |

The browser labs deliberately make you do this work **by hand first**. Reaching
for a skill before you know what good looks like is the exact failure Module 2
warns about.

## License

MIT. See [LICENSE](LICENSE).
