"""Assert that every failure mode the course teaches is actually reachable.

Run:  python scripts/check_coverage.py

A synthetic world can look rich and still be missing whole branches of its own
rulebook. When we first generated this world, no delivery was older than about
nine months, so a twelve-month warranty could never lapse and the
`warranty_expired` outcome was unreachable. Nothing failed. The corpus simply
had a silent hole where a failure mode should have been.

This script is the guard. It enumerates every decision and reason the rules can
emit, counts how often each is actually produced over the real world database,
and exits non-zero if any is missing or too rare to sample.

Treat a failure here as a bug in the world generator, not in this script.
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data" / "world"))

import rules  # noqa: E402

DB = ROOT / "data" / "world" / "wayfarer.db"
FACTS = ROOT / "data" / "world" / "facts.yaml"

# Minimum occurrences for an outcome to be considered usable. Anything that
# fires once or twice cannot support a train/dev/test split later.
MIN_COUNT = 15

EXPECTED_RETURN_DECISIONS = {
    "eligible", "not_eligible", "route_to_warranty", "escalate",
}

EXPECTED_RETURN_REASONS = {
    "outside_window", "final_sale", "condition_damaged", "item_recalled",
    "hazmat_ground_label_required", "order_status_pending",
    "order_status_shipped", "order_status_cancelled",
}

EXPECTED_WARRANTY_DECISIONS = {"covered", "denied", "not_eligible"}

EXPECTED_WARRANTY_REASONS = {
    "lifetime_warranty", "within_warranty", "warranty_expired",
    "excluded_ordinary_wear", "excluded_lost", "reason_not_covered",
    "order_status_pending", "order_status_shipped", "order_status_cancelled",
}

# Claim reasons to probe. These must span all three branches of the rulebook:
# a covered defect, an explicitly excluded reason, and a reason that appears in
# neither list. Without that third kind, `reason_not_covered` is unreachable
# and the check reports a hole that does not exist in the world.
WARRANTY_PROBE_REASONS = ("defect", "seam_failure", "ordinary_wear", "lost", "changed_mind")

EXPECTED_AUTH_DECISIONS = {"allowed", "escalate", "denied"}


def report(title: str, counts: Counter, expected: set[str], min_count: int = MIN_COUNT) -> list[str]:
    """Report coverage of `expected` against observed `counts`.

    `min_count` is per-report because some checks are census-style (every order
    item in the world) while others are probe-style (a fixed handful of
    boundary values). Applying a census threshold to a probe set produces noise,
    not signal.
    """
    print(f"\n{title}")
    problems = []
    for name in sorted(expected):
        n = counts.get(name, 0)
        if n == 0:
            mark, note = "MISSING ", "  <-- unreachable"
            problems.append(f"{title}: '{name}' is unreachable")
        elif n < min_count:
            mark, note = "THIN    ", f"  <-- below {min_count}"
            problems.append(f"{title}: '{name}' only occurs {n} times")
        else:
            mark, note = "ok      ", ""
        print(f"  {mark} {name:34s} {n:6d}{note}")
    extra = sorted(set(counts) - expected)
    for name in extra:
        print(f"  EXTRA    {name:34s} {counts[name]:6d}  <-- not in expected set")
        problems.append(f"{title}: unexpected outcome '{name}'")
    return problems


def main() -> int:
    facts = yaml.safe_load(FACTS.read_text())
    conn = sqlite3.connect(DB)
    item_ids = [r[0] for r in conn.execute("SELECT id FROM order_items")]

    ret_dec, ret_why = Counter(), Counter()
    war_dec, war_why = Counter(), Counter()

    for iid in item_ids:
        o = rules.return_eligibility(conn, facts, iid)
        ret_dec[o.decision] += 1
        ret_why.update(o.reasons)

    # Exercise warranty across all three reason classes, otherwise the
    # excluded and not-covered branches never appear.
    for iid in item_ids:
        for reason in WARRANTY_PROBE_REASONS:
            o = rules.warranty_eligibility(conn, facts, iid, reason)
            war_dec[o.decision] += 1
            war_why.update(o.reasons)

    auth_dec = Counter()
    limit = facts["authorization"]["refund_auto_approve_limit_cents"]
    ceiling = facts["authorization"]["refund_hard_ceiling_cents"]
    probes = [
        ("support", 1), ("support", limit - 1), ("support", limit),
        ("support", limit + 1), ("support", ceiling), ("support", ceiling + 1),
        ("support", 0), ("support", -100), ("analyst", 1000),
    ]
    for role, amt in probes:
        auth_dec[rules.refund_authorization(facts, role, amt).decision] += 1

    print(f"world: {len(item_ids)} order items")
    problems = []
    problems += report("return decisions", ret_dec, EXPECTED_RETURN_DECISIONS)
    problems += report("return reasons", ret_why, EXPECTED_RETURN_REASONS)
    problems += report("warranty decisions", war_dec, EXPECTED_WARRANTY_DECISIONS)
    problems += report("warranty reasons", war_why, EXPECTED_WARRANTY_REASONS)
    # Probe-style: nine fixed boundary values, so presence is the bar, not volume.
    problems += report("authorization decisions", auth_dec, EXPECTED_AUTH_DECISIONS, min_count=1)

    # Boundary assertions. These are exact and must never soften.
    print("\nboundary assertions")
    checks = [
        ("refund at limit is allowed",
         rules.refund_authorization(facts, "support", limit).decision == "allowed"),
        ("refund one cent over limit escalates",
         rules.refund_authorization(facts, "support", limit + 1).decision == "escalate"),
        ("refund over hard ceiling is denied",
         rules.refund_authorization(facts, "support", ceiling + 1).decision == "denied"),
        ("analyst may never refund",
         rules.refund_authorization(facts, "analyst", 1).decision == "denied"),
        ("zero refund is denied",
         rules.refund_authorization(facts, "support", 0).decision == "denied"),
    ]
    for label, ok in checks:
        print(f"  {'ok      ' if ok else 'FAILED  '} {label}")
        if not ok:
            problems.append(f"boundary assertion failed: {label}")

    if problems:
        print(f"\n{len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("\nall outcomes reachable and above threshold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
