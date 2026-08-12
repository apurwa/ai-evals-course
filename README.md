# AI Evals Course

A hands-on, open-source course on evaluating and improving LLM-powered products.
You build a support agent, instrument it, find its failures with a disciplined
error-analysis process, put it under a regression suite, red-team it, and then
improve both accuracy and cost.

Two ways to take it.

## Track A: run it in your browser

Nothing to install. No API key. No Docker.

Open `docs/index.html`, or visit the published site. Every lab runs client-side
against a corpus of real agent traces committed to this repository. You will
annotate real traces, build a failure taxonomy, write and score judge prompts,
and attack a permission layer that genuinely enforces.

## Track B: run it on your machine

```bash
git clone https://github.com/apurwa/ai-evals-course
cd ai-evals-course
pip install -r requirements.txt

make world     # build the deterministic world, no API key needed
make check     # run every correctness gate, no API key needed
```

`make check` should print `all gates passed`. If it does not, stop and read the
output rather than continuing, because everything downstream assumes those
invariants hold.

To run the agent and generate your own traces you will need an OpenAI API key
and Docker. See `docs/lessons/01-foundations.html`.

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
