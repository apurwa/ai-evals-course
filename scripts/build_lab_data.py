"""Lab data for L3 (scenario dimensions) and L9 (cost). No API key.

Run:  python scripts/build_lab_data.py

Two separate things, both small enough that a third generator script would be
more ceremony than it is worth.

L3 needs the dimension profiles behind the 500 support scenarios, so the
coverage lab can show which combinations exist in the world, which are sampled,
and which are absent. Absent is the interesting category: a combination the
world cannot produce is a hole in your test suite that no amount of running it
will reveal.

L9 needs per-step cost, which is computed from the corpus rather than invented.
Read the honesty note on `configs` below before using that part.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data" / "world"))

DB = ROOT / "data" / "world" / "wayfarer.db"
FACTS_PATH = ROOT / "data" / "world" / "facts.yaml"
SCEN = ROOT / "data" / "scenarios" / "support.jsonl"
CORPUS = ROOT / "docs" / "data" / "lab-corpus.json"
OUT = ROOT / "docs" / "data" / "lab-data"

DIMS = ["tier", "condition", "order_status", "final_sale", "hazmat", "recalled", "decision"]


def build_l3() -> dict:
    """Every dimension profile the world contains, and how the corpus samples it."""
    import rules  # noqa: E402

    facts = yaml.safe_load(FACTS_PATH.read_text())
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # What the world can actually produce, over every order line.
    world = Counter()
    for (iid,) in conn.execute("SELECT id FROM order_items"):
        ctx = rules.item_context(conn, iid)
        out = rules.return_eligibility(conn, facts, iid)
        world[(ctx["tier"], ctx["condition"] or "none", ctx["order_status"],
               bool(ctx["is_final_sale"]), bool(ctx["is_hazmat"]),
               bool(ctx["is_recalled"]), out.decision)] += 1
    conn.close()

    # What the scenario set samples, split by sampling strategy. These are not
    # interchangeable and L3 is largely about why.
    sampled: dict[tuple, Counter] = {}
    for line in SCEN.read_text().splitlines():
        r = json.loads(line)
        p = r.get("profile")
        if not p:
            continue
        key = tuple(p)
        sampled.setdefault(key, Counter())[r.get("sampling", "unknown")] += 1

    rows = []
    for prof, n in sorted(world.items(), key=lambda kv: (-kv[1], str(kv[0]))):
        s = sampled.get(prof, Counter())
        rows.append({
            "profile": dict(zip(DIMS, prof)),
            "world_count": n,
            "sampled": dict(s),
            "sampled_total": sum(s.values()),
        })

    # The combinations the rulebook allows but the world never produced. These
    # are the holes, and they are invisible from inside the scenario set.
    tiers = sorted({p[0] for p in world})
    conds = sorted({p[1] for p in world})
    statuses = sorted({p[2] for p in world})
    possible = len(tiers) * len(conds) * len(statuses) * 2 * 2 * 2

    return {
        "dims": DIMS,
        "rows": rows,
        "distinct_in_world": len(world),
        "distinct_sampled": len(sampled),
        "combinatorial_space": possible,
        "axes": {"tier": tiers, "condition": conds, "order_status": statuses},
    }


def build_l9() -> dict:
    """Real per-step cost from the corpus, plus clearly-labelled what-ifs."""
    corpus = json.loads(CORPUS.read_text())
    traces = corpus["traces"]

    by_step, by_kind, by_scenario_kind = Counter(), Counter(), Counter()
    tool_counts = Counter()
    per_trace = []

    for t in traces:
        for s in t["spans"]:
            c = s.get("cost_usd") or 0
            by_kind[s["kind"]] += c
            if s["kind"] == "model_call":
                by_step[s["attributes"].get("step", 0)] += c
            if s["kind"] == "tool_call":
                tool_counts[s["attributes"].get("tool", "?")] += 1
        by_scenario_kind[t["scenario"]["kind"]] += t["totals"]["cost_usd"]
        per_trace.append({
            "id": t["scenario"]["id"],
            "kind": t["scenario"]["kind"],
            "cost": round(t["totals"]["cost_usd"], 6),
            "model_calls": t["totals"]["model_calls"],
            "tool_calls": t["totals"]["tool_calls"],
            "correct": t["label"]["correct"],
        })

    per_trace.sort(key=lambda r: -r["cost"])
    total = sum(r["cost"] for r in per_trace)

    return {
        "total_cost": round(total, 6),
        "trace_count": len(per_trace),
        "by_span_kind": {k: round(v, 6) for k, v in by_kind.items()},
        "by_model_step": {str(k): round(v, 6) for k, v in sorted(by_step.items())},
        "by_scenario_kind": {k: round(v, 6) for k, v in by_scenario_kind.most_common()},
        "tool_frequency": dict(tool_counts.most_common()),
        "traces": per_trace,

        # HONESTY NOTE, and the labs repeat it on the page.
        #
        # These configurations are NOT measurements. Nothing in this repository
        # has run against a second model, so no accuracy number below was
        # observed. They are illustrative points used to teach the shape of a
        # frontier and the mechanics of choosing on it.
        #
        # They are kept because the reasoning skill (which point on the curve is
        # right for which product decision) is worth practising before you have
        # spent money, and because the L9 local lab replaces every one of these
        # rows with real numbers from the learner's own corpus run. What must
        # not happen is anyone quoting these as evidence about real models.
        "configs_are_illustrative": True,
        "configs": [
            {"name": "mid-tier, 8 steps", "accuracy": 0.79, "cost_per_1k": 4.80, "note": "the baseline in this repository"},
            {"name": "mid-tier, 4 steps", "accuracy": 0.71, "cost_per_1k": 2.60, "note": "cheaper, loses multi-step recoveries"},
            {"name": "mid-tier + policy cache", "accuracy": 0.80, "cost_per_1k": 3.40, "note": "same quality, fewer tokens"},
            {"name": "frontier, 8 steps", "accuracy": 0.93, "cost_per_1k": 41.00, "note": "best quality, roughly 9x the cost"},
            {"name": "frontier, judge-gated retry", "accuracy": 0.95, "cost_per_1k": 58.00, "note": "diminishing returns begin here"},
            {"name": "small model, 8 steps", "accuracy": 0.52, "cost_per_1k": 1.10, "note": "cheap and not usable"},
            {"name": "router: small then frontier", "accuracy": 0.89, "cost_per_1k": 12.50, "note": "escalate only hard cases"},
        ],
    }


def main() -> int:
    if not DB.exists():
        raise SystemExit("world database missing; run make world first")
    if not CORPUS.exists():
        raise SystemExit("lab corpus missing; run python scripts/build_lab_corpus.py first")

    data = {"l3": build_l3(), "l9": build_l9()}

    l3 = data["l3"]
    if l3["distinct_in_world"] < 50:
        raise SystemExit(f"only {l3['distinct_in_world']} profiles; the coverage grid needs more")
    if l3["distinct_in_world"] >= l3["combinatorial_space"]:
        raise SystemExit(
            "the world produces every combination the rulebook allows, so the L3 lab has no "
            "holes to find and its central exercise is impossible"
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True)
    OUT.with_suffix(".json").write_text(payload + "\n")
    OUT.with_suffix(".js").write_text(
        "// Generated by scripts/build_lab_data.py. Do not edit.\n"
        f"window.LAB_DATA = {payload};\n"
    )

    print(f"wrote {OUT.with_suffix('.json').relative_to(ROOT)}")
    print(f"  L3: {l3['distinct_in_world']} profiles in the world, "
          f"{l3['distinct_sampled']} sampled, {l3['combinatorial_space']} combinatorially possible")
    print(f"  L9: {data['l9']['trace_count']} traces, ${data['l9']['total_cost']:.4f} total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
