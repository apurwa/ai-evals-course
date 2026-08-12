"""Assert that the enforcement layer and the ground-truth rules never diverge.

Run:  python scripts/check_permissions.py

The same refund policy is written down three times in this repository:

    facts.yaml            the numbers
    rules.py              what the correct answer is
    permissions.py        what actually gets enforced

Three copies of one policy is how policies drift. A limit gets raised in one
place, the grader keeps scoring against the old value, and the eval quietly
starts rewarding the wrong behavior. Nothing crashes.

This script sweeps the entire decision space and asserts all three agree. It is
wired into CI and should be treated as a release gate, not a nicety.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data" / "world"))
sys.path.insert(0, str(ROOT / "agent"))

import rules            # noqa: E402
from permissions import PermissionLayer  # noqa: E402

FACTS = ROOT / "data" / "world" / "facts.yaml"


def equivalent(rules_decision: str, allowed: bool, requires_approval: bool) -> bool:
    """Map a PermissionLayer Decision onto the rules vocabulary."""
    if rules_decision == "allowed":
        return allowed
    if rules_decision == "escalate":
        return (not allowed) and requires_approval
    if rules_decision == "denied":
        return (not allowed) and not requires_approval
    return False


def main() -> int:
    facts = yaml.safe_load(FACTS.read_text())
    limit = facts["authorization"]["refund_auto_approve_limit_cents"]
    ceiling = facts["authorization"]["refund_hard_ceiling_cents"]

    # Sweep the boundaries exactly, plus a coarse sweep across the range. Off
    # by one at a limit is the single most common authorization bug, so every
    # boundary is probed at limit-1, limit, and limit+1.
    amounts = sorted({
        -1000, -1, 0, 1, 2,
        limit - 1, limit, limit + 1,
        ceiling - 1, ceiling, ceiling + 1,
        *range(0, ceiling + 20000, 5000),
    })
    roles = list(facts["authorization"]["roles"])

    layers = {r: PermissionLayer(r, facts) for r in roles}
    mismatches = []
    counts = {"allowed": 0, "escalate": 0, "denied": 0}

    for role in roles:
        layer = layers[role]
        for amt in amounts:
            expected = rules.refund_authorization(facts, role, amt)
            got = layer.authorize("issue_refund", {"amount_cents": amt})
            counts[expected.decision] = counts.get(expected.decision, 0) + 1
            if not equivalent(expected.decision, got.allowed, got.requires_approval):
                mismatches.append(
                    f"role={role} amount={amt}: rules said {expected.decision} "
                    f"({','.join(expected.reasons)}) but permissions said "
                    f"allowed={got.allowed} approval={got.requires_approval} ({got.reason})"
                )

    print(f"swept {len(amounts)} amounts x {len(roles)} roles = {len(amounts) * len(roles)} cases")
    for k, v in counts.items():
        print(f"  {k:10s} {v}")

    # Independent assertions that do not depend on rules.py, so a matching pair
    # of wrong implementations still gets caught.
    print("\nindependent assertions")
    support = layers["support"]
    analyst = layers["analyst"]
    checks = [
        ("support may read an order",
         support.authorize("lookup_order", {}).allowed),
        ("analyst may not look up an individual customer",
         not analyst.authorize("lookup_customer", {}).allowed),
        ("support may not run analytics",
         not support.authorize("run_analytics_query", {}).allowed),
        ("tier 3 always requires a human",
         support.authorize("cancel_order_final", {}).requires_approval),
        ("unknown tool defaults to tier 3",
         support.tier_of("some_tool_nobody_classified") == 3),
        ("unknown tool is refused",
         not support.authorize("some_tool_nobody_classified", {}).allowed),
        ("hazmat air label refused",
         not support.authorize("create_return_label",
                                {"is_hazmat": True, "method": "air"}).allowed),
        ("hazmat ground label allowed",
         support.authorize("create_return_label",
                           {"is_hazmat": True, "method": "ground"}).allowed),
        ("address edit on shipped order refused",
         not support.authorize("update_shipping_address",
                               {"shipment_status": "in_transit"}).allowed),
        ("address edit on pending order allowed",
         support.authorize("update_shipping_address",
                           {"shipment_status": "pending"}).allowed),
        ("refund above computed line refund refused",
         not support.authorize("issue_refund",
                               {"amount_cents": 9000, "computed_refund_cents": 7500}).allowed),
        ("refund at computed line refund allowed",
         support.authorize("issue_refund",
                           {"amount_cents": 7500, "computed_refund_cents": 7500}).allowed),
        ("non-integer amount refused",
         not support.authorize("issue_refund", {"amount_cents": 100.5}).allowed),
        ("role is not settable",
         not hasattr(type(support).role, "fset") or type(support).role.fset is None),
    ]
    failed = []
    for label, ok in checks:
        print(f"  {'ok      ' if ok else 'FAILED  '} {label}")
        if not ok:
            failed.append(label)

    if mismatches:
        print(f"\n{len(mismatches)} drift mismatch(es) between rules.py and permissions.py:")
        for m in mismatches[:20]:
            print(f"  - {m}")
    if failed:
        print(f"\n{len(failed)} assertion(s) failed")

    if mismatches or failed:
        return 1
    print("\nrules.py and permissions.py agree on every case")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
