"""Run scenarios against a real model and write the trace corpus.

    python scripts/run_corpus.py --estimate         # costs nothing, prints a forecast
    python scripts/run_corpus.py --limit 10         # smoke run, cents
    python scripts/run_corpus.py                    # the full corpus

THIS SPENDS YOUR MONEY. It is the only script in the repository that does, and
it is built to be hard to regret:

  --estimate      forecasts tokens and dollars without calling anything. Always
                  the first thing to run.
  --limit N       runs N scenarios. The default flow is a smoke run first,
                  because a prompt bug found on scenario 3 costs a cent and the
                  same bug found on scenario 650 costs the whole run.
  --max-usd       hard ceiling. The run stops when projected spend crosses it,
                  mid-corpus, and writes what it has.
  resume          completed scenarios are skipped on a rerun, so an interrupted
                  run is not a wasted one.

On prices being unverified: evals/pricing.json is marked verified: false and
warns on every run. Cost figures here are arithmetic against unconfirmed
numbers. The token counts are real measurements; the dollars are an estimate
with a known source of error. Your provider's dashboard is the authority.

WHAT THIS PRODUCES that the scripted corpus cannot: a failure distribution
nobody chose. The taxonomy a learner discovers in L4 against these traces is
one the model produced, which is the entire difference between practising error
analysis and doing it.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "data" / "world"))

from support_agent import (  # noqa: E402
    DEFAULT_MODEL, MAX_STEPS, OpenAIClient, load_pricing, run_scenario,
)

FACTS_PATH = ROOT / "data" / "world" / "facts.yaml"
SCEN_DIR = ROOT / "data" / "scenarios"
OUT_DIR = ROOT / "data" / "corpus"

# Measured from the scripted corpus, which uses the same system prompt, the
# same tool schemas and the same loop. Real runs vary with how many steps the
# model takes, so treat this as an order of magnitude, not a quote.
EST_INPUT_PER_SCENARIO = 9_800
EST_OUTPUT_PER_SCENARIO = 420


def load_scenarios(role: str | None, limit: int | None, kinds: list[str] | None) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(SCEN_DIR.glob("*.jsonl")):
        if role and path.stem != role:
            continue
        rows.extend(json.loads(l) for l in path.read_text().splitlines())
    if kinds:
        rows = [r for r in rows if r.get("kind") in kinds]
    rows.sort(key=lambda r: r["id"])
    return rows[:limit] if limit else rows


def already_done(out_path: Path) -> set[str]:
    if not out_path.exists():
        return set()
    done = set()
    for line in out_path.read_text().splitlines():
        try:
            done.add(json.loads(line)["scenario_id"])
        except Exception:  # noqa: BLE001
            continue  # a partial final line from an interrupted run
    return done


def estimate(rows: list[dict], model: str) -> None:
    pricing = load_pricing(model)
    n = len(rows)
    tin, tout = n * EST_INPUT_PER_SCENARIO, n * EST_OUTPUT_PER_SCENARIO
    cost = pricing.cost(tin, tout, 0)
    print(f"\nmodel        {model}")
    print(f"scenarios    {n}")
    print(f"input        ~{tin:,} tokens")
    print(f"output       ~{tout:,} tokens")
    print(f"ESTIMATE     ~${cost:.2f}")
    print(f"with retries ~${cost * 1.3:.2f}")
    print("\nPrices are from evals/pricing.json, which is marked unverified.")
    print("Run a smoke test before the full corpus:")
    print("    python scripts/run_corpus.py --limit 10")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--limit", type=int, help="run only the first N scenarios")
    ap.add_argument("--role", choices=["support", "analyst"])
    ap.add_argument("--kind", action="append", help="filter by scenario kind, repeatable")
    ap.add_argument("--max-usd", type=float, default=10.0, help="hard spend ceiling")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-steps", type=int, default=MAX_STEPS)
    ap.add_argument("--estimate", action="store_true", help="forecast only, calls nothing")
    ap.add_argument("--fresh", action="store_true", help="ignore previous output and start over")
    args = ap.parse_args()

    rows = load_scenarios(args.role, args.limit, args.kind)
    if not rows:
        raise SystemExit("no scenarios matched; run make scenarios first")

    if args.estimate:
        estimate(rows, args.model)
        return 0

    facts = yaml.safe_load(FACTS_PATH.read_text())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"traces-{args.model}.jsonl"

    if args.fresh and out_path.exists():
        out_path.unlink()

    done = already_done(out_path)
    todo = [r for r in rows if r["id"] not in done]
    if done:
        print(f"resuming: {len(done)} already done, {len(todo)} to go")
    if not todo:
        print("nothing to do; every scenario in this selection already has a trace")
        return 0

    client = OpenAIClient()
    pricing = load_pricing(args.model)

    lock = threading.Lock()
    spent = [0.0]
    stop = threading.Event()
    counts: Counter = Counter()
    fh = out_path.open("a")

    def work(sc: dict) -> None:
        if stop.is_set():
            return
        try:
            trace = run_scenario(sc, client, model=args.model, facts=facts,
                                 max_steps=args.max_steps)
        except Exception as e:  # noqa: BLE001
            # One scenario failing must not lose the traces already paid for.
            with lock:
                counts["error"] += 1
                print(f"  {sc['id']}  ERROR  {type(e).__name__}: {e}", file=sys.stderr)
            return

        rec = trace.to_dict()
        rec["scenario"] = {"id": sc["id"], "kind": sc.get("kind"),
                           "message": sc["message"], "role": sc.get("role"),
                           "sampling": sc.get("sampling"), "order_id": sc.get("order_id")}
        rec["expected"] = sc["expected"]
        rec["totals"] = trace.totals

        with lock:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            fh.flush()          # so an interrupted run resumes cleanly
            spent[0] += trace.totals["cost_usd"]
            counts["done"] += 1
            counts["denials"] += trace.totals["permission_denials"]
            n = counts["done"]
            if n % 10 == 0 or n == len(todo):
                print(f"  {n}/{len(todo)}  ${spent[0]:.4f}")
            if spent[0] >= args.max_usd and not stop.is_set():
                stop.set()
                print(f"\nSTOPPING: spend reached the --max-usd ceiling of "
                      f"${args.max_usd:.2f}. {n} traces written.", file=sys.stderr)

    print(f"running {len(todo)} scenarios against {args.model}, "
          f"{args.workers} workers, ceiling ${args.max_usd:.2f}")
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(work, sc) for sc in todo]
            for f in as_completed(futures):
                f.result()
    except KeyboardInterrupt:
        stop.set()
        print("\ninterrupted; traces so far are saved and a rerun will resume", file=sys.stderr)
    finally:
        fh.close()

    # ---- smoke report
    traces = [json.loads(l) for l in out_path.read_text().splitlines() if l.strip()]
    print("\n" + "=" * 58)
    print(f"corpus: {out_path.relative_to(ROOT)}")
    print(f"  traces          {len(traces)}")
    print(f"  spent this run  ${spent[0]:.4f}   (unverified prices)")
    print(f"  errors          {counts['error']}")

    if traces:
        no_final = sum(1 for t in traces if not t.get("final_response"))
        denials = sum(t["totals"]["permission_denials"] for t in traces)
        steps = sum(t["totals"]["model_calls"] for t in traces) / len(traces)
        print(f"  denials         {denials}")
        print(f"  no final answer {no_final}")
        print(f"  mean model calls {steps:.1f}")
        kinds = Counter(t["scenario"]["kind"] for t in traces)
        print("\n  by kind")
        for k, v in kinds.most_common():
            print(f"    {k:<22} {v}")

        # Health checks on the run itself, not on the agent. A corpus that is
        # uniformly perfect or uniformly broken usually means the harness is
        # wrong, not that the agent is remarkable.
        print("\n  smoke checks")
        problems = []
        if no_final / len(traces) > 0.25:
            problems.append(f"{no_final} of {len(traces)} runs produced no final answer; "
                            "check --max-steps and the tool schemas")
        if steps < 1.5:
            problems.append("mean model calls below 1.5: the model may not be calling tools "
                            "at all, which usually means the schemas are not reaching it")
        if counts["error"] > len(traces) * 0.05:
            problems.append(f"{counts['error']} errors, over 5 percent")
        for p in problems:
            print(f"    PROBLEM  {p}")
        if not problems:
            print("    ok       the harness looks healthy; grade with your evals next")

    print("\nnext:")
    print("  compare this distribution with the scripted one in docs/data/lab-corpus.json.")
    print("  the failures here were not chosen by anyone. that is the point of L4.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
