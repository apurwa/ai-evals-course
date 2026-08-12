"""Build the trace corpus the Module 2 to Module 5 labs run on. No API key.

Run:  python scripts/build_lab_corpus.py

WHAT THIS IS, PRECISELY

Every tool call here is chosen by this file, not by a language model. What runs
underneath is real: the control loop in agent/support_agent.py, the permission
layer in agent/permissions.py, the database, and rules.py for ground truth. A
denial in this corpus is a real denial. A refund amount is really wrong, by
comparison with a really computed correct answer.

What is NOT real is the distribution. A model's failures cluster in ways nobody
can predict from a spec, which is the entire reason error analysis is a manual
process rather than a checklist. Here the failure modes were chosen in advance,
so the taxonomy a learner discovers in L4 is a taxonomy this file planted.

That is a real limitation and the labs say so on the page. It is still worth
doing, for three reasons:

  1. The mechanics are identical. Open coding, axial clustering, saturation,
     judge alignment, and TPR/TNR all work the same on planted failures as on
     found ones. The skill transfers even though the discovery does not.

  2. Ground truth exists by construction, so L5 can compute a judge's true
     positive and true negative rates exactly. With model-generated traces you
     would have to hand-label a few hundred first, which is L4's homework, not
     something to hand a learner in their first hour.

  3. It works with no API key, so the browser track is a real course rather
     than a brochure for the local one.

When a learner runs `make corpus` with their own key, the same labs point at
their traces instead and the planted distribution is replaced by a found one.
That contrast is the point of the L4 wrap-up.

DETERMINISM

Seeded from facts.yaml's world seed and normalized like the L1 fixture, so the
committed artifact is byte-identical on every machine. CI checks this.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "data" / "world"))

from support_agent import ScriptedClient, run_scenario  # noqa: E402

SCEN = ROOT / "data" / "scenarios" / "support.jsonl"
FACTS_PATH = ROOT / "data" / "world" / "facts.yaml"
OUT = ROOT / "docs" / "data" / "lab-corpus"

TARGET = 120


# ---------------------------------------------------------------------------
# The failure taxonomy
# ---------------------------------------------------------------------------
#
# Written down here because L4 asks learners to discover it. Keep the labels
# out of anything the L4 annotation lab shows before a learner has coded a
# trace, or the exercise becomes a reading comprehension test.
#
# `severity` is a product judgment, not a technical one, and it drives the L7
# gate design lab. Money moving wrongly outranks a customer being told the
# right thing for the wrong reason.

FAILURES = {
    "skipped_eligibility_check": dict(
        severity="high",
        blurb="Answered a returns question without calling check_return_eligibility."),
    "hallucinated_restocking_fee": dict(
        severity="critical",
        blurb="Invented a restocking percentage and quoted it as policy."),
    "wrong_reason_cited": dict(
        severity="medium",
        blurb="Correct decision, but the reason given to the customer is not the reason."),
    "hazmat_air_label_attempt": dict(
        severity="high",
        blurb="Tried to ship a hazmat item by air."),
    "over_limit_refund_attempt": dict(
        severity="high",
        blurb="Tried to pay a refund above its own authority."),
    "under_refund": dict(
        severity="critical",
        blurb="Refunded less than the customer is owed. Passes every permission check."),
    "missed_escalation": dict(
        severity="critical",
        blurb="Policy required a handoff and the agent closed the conversation instead."),
    "warranty_misroute": dict(
        severity="high",
        blurb="Damaged item routed as a return rather than a warranty claim."),
    "recall_not_flagged": dict(
        severity="critical",
        blurb="Recalled item handled as an ordinary return. Safety issue, not a service one."),
    "policy_from_memory": dict(
        severity="medium",
        blurb="Stated a policy value without calling get_policy."),
    "premature_closure": dict(
        severity="medium",
        blurb="Ended the conversation without answering the question asked."),
    "answered_out_of_scope": dict(
        severity="low",
        blurb="Gave product advice it is not meant to give."),
}


def money(cents: int) -> str:
    return f"${cents / 100:.2f}"


# ---------------------------------------------------------------------------
# Script builders, one per scenario kind
# ---------------------------------------------------------------------------
#
# Each returns (script, failure_mode_or_None). The script is a list of model
# turns; run_scenario feeds them through the real loop and the real gate.

def _look(order_id: int) -> dict:
    return {"content": "Let me pull that order up.",
            "tool_calls": [{"name": "lookup_order", "arguments": {"order_id": order_id}}],
            "input_tokens": 1180, "output_tokens": 24}


def _elig(item_id: int) -> dict:
    return {"content": "Checking the authoritative decision.",
            "tool_calls": [{"name": "check_return_eligibility",
                            "arguments": {"order_item_id": item_id}}],
            "input_tokens": 1610, "output_tokens": 30}


def _say(text: str, out: int = 80) -> dict:
    return {"content": text, "input_tokens": 2200, "output_tokens": out}


def build_return(sc: dict, mode: str | None) -> list[dict]:
    oid, iid = sc["order_id"], sc["order_item_id"]
    f = sc["expected"]["facts"]
    reasons = sc["expected"]["reasons"]
    decision = sc["expected"]["decision"]
    s = [_look(oid)]

    if mode == "skipped_eligibility_check":
        return s + [_say(
            "Looking at this, I think you should be fine to send it back. "
            "Pop it in any box and we will sort the refund out once it lands.")]

    if mode == "policy_from_memory":
        return s + [_say(
            "Our returns window is 30 days from delivery for everyone, so you are "
            "outside it. I am afraid there is nothing I can do here.")]

    if mode == "recall_not_flagged":
        return s + [_elig(iid), _say(
            "Happy to help with that return. I will get a label over to you and "
            "the refund will follow once we have it back.")]

    if mode == "warranty_misroute":
        return s + [_elig(iid), _say(
            "Sorry about that. Since it arrived damaged I have started an ordinary "
            "return for you, so send it back and we will refund it.")]

    s.append(_elig(iid))

    if mode == "hazmat_air_label_attempt":
        s.append({"content": "Getting that label sorted, express.",
                  "tool_calls": [{"name": "create_return_label",
                                  "arguments": {"order_item_id": iid, "method": "air"}}],
                  "input_tokens": 2050, "output_tokens": 32})
        s.append({"content": "Ground it is.",
                  "tool_calls": [{"name": "create_return_label",
                                  "arguments": {"order_item_id": iid, "method": "ground"}}],
                  "input_tokens": 2280, "output_tokens": 28})
        return s + [_say("Your return is approved and the label is on its way.")]

    if mode == "hallucinated_restocking_fee":
        bogus = f["gross_cents"] // 2
        s.append({"content": "Applying the restocking fee.",
                  "tool_calls": [{"name": "issue_refund",
                                  "arguments": {"order_item_id": iid, "amount_cents": bogus}}],
                  "input_tokens": 2100, "output_tokens": 34})
        return s + [_say(
            f"Used items carry a 50% restocking fee, so I have refunded {money(bogus)} "
            "to your original payment method.")]

    if mode == "wrong_reason_cited":
        return s + [_say(
            "Unfortunately that one falls outside our 30 day returns window, so I am "
            "not able to accept it back.")]

    if decision == "eligible":
        method = "ground"
        s.append({"content": "Creating the label.",
                  "tool_calls": [{"name": "create_return_label",
                                  "arguments": {"order_item_id": iid, "method": method}}],
                  "input_tokens": 2000, "output_tokens": 30})
        refund = f.get("refund_cents", 0)
        if refund > 20000:
            s.append({"content": "This needs sign-off.",
                      "tool_calls": [{"name": "escalate_to_human",
                                      "arguments": {"reason": "above_auto_approve_limit",
                                                    "summary": f"Refund {money(refund)} exceeds limit."}}],
                      "input_tokens": 2300, "output_tokens": 60})
            return s + [_say(
                f"Your return is approved and a label is on its way. The refund of "
                f"{money(refund)} needs a quick supervisor approval, so a colleague "
                "will confirm shortly.")]
        s.append({"content": "Refunding.",
                  "tool_calls": [{"name": "issue_refund",
                                  "arguments": {"order_item_id": iid, "amount_cents": refund}}],
                  "input_tokens": 2200, "output_tokens": 32})
        fee = f.get("restock_fee_pct", 0)
        note = f" after the {fee}% restocking fee for its condition" if fee else ""
        return s + [_say(
            f"All set. Your return is approved and I have refunded {money(refund)}{note}.")]

    if decision == "route_to_warranty":
        s.append({"content": "This is a warranty matter.",
                  "tool_calls": [{"name": "escalate_to_human",
                                  "arguments": {"reason": "warranty_route",
                                                "summary": "Damaged on arrival, route to warranty."}}],
                  "input_tokens": 2200, "output_tokens": 55})
        return s + [_say(
            "Since it arrived damaged this goes through our warranty team rather than "
            "a standard return. I have passed it to them and they will be in touch.")]

    if decision == "escalate":
        s.append({"content": "Escalating.",
                  "tool_calls": [{"name": "escalate_to_human",
                                  "arguments": {"reason": ",".join(reasons),
                                                "summary": "Requires human review."}}],
                  "input_tokens": 2200, "output_tokens": 55})
        return s + [_say(
            "I need to get a colleague to look at this one with you. I have passed "
            "the details across and someone will follow up shortly.")]

    reason_text = {
        "final_sale": "this item was a final sale, which we are not able to accept back",
        "outside_window": "it is past the returns window for your order",
        "order_status_pending": "the order has not shipped yet, so there is nothing to return",
        "order_status_shipped": "it is still in transit, so we cannot start a return yet",
        "order_status_cancelled": "that order was cancelled, so there is nothing to return",
        "condition_damaged": "damaged items go through warranty rather than returns",
    }.get(reasons[0] if reasons else "", "we are not able to accept this one back")
    return s + [_say(f"I am sorry, but {reason_text}.")]


def build_refund(sc: dict, mode: str | None) -> list[dict]:
    oid, iid = sc["order_id"], sc["order_item_id"]
    f = sc["expected"]["facts"]
    refund = f.get("refund_cents", f.get("amount_cents", 0))
    s = [_look(oid), _elig(iid)]

    if mode == "over_limit_refund_attempt":
        s.append({"content": "Paying that out now.",
                  "tool_calls": [{"name": "issue_refund",
                                  "arguments": {"order_item_id": iid, "amount_cents": refund}}],
                  "input_tokens": 2200, "output_tokens": 34})
        s.append({"content": "Escalating instead.",
                  "tool_calls": [{"name": "escalate_to_human",
                                  "arguments": {"reason": "above_auto_approve_limit",
                                                "summary": f"Refund {money(refund)} above limit."}}],
                  "input_tokens": 2500, "output_tokens": 58})
        return s + [_say(
            f"Your refund of {money(refund)} needs a supervisor to approve it, so a "
            "colleague will confirm that shortly.")]

    if mode == "under_refund":
        short = max(100, int(refund * 0.6))
        s.append({"content": "Refunding the balance.",
                  "tool_calls": [{"name": "issue_refund",
                                  "arguments": {"order_item_id": iid, "amount_cents": short}}],
                  "input_tokens": 2200, "output_tokens": 34})
        return s + [_say(
            f"Done. I have put {money(short)} back on your original payment method "
            "after deductions.")]

    if mode == "missed_escalation":
        return s + [_say(
            f"That is all sorted, your refund of {money(refund)} is on its way back "
            "to you and should land in three to five working days.")]

    if sc["expected"]["decision"] == "escalate":
        s.append({"content": "Escalating.",
                  "tool_calls": [{"name": "escalate_to_human",
                                  "arguments": {"reason": "above_auto_approve_limit",
                                                "summary": f"Refund {money(refund)} above limit."}}],
                  "input_tokens": 2300, "output_tokens": 58})
        return s + [_say(
            f"Your refund of {money(refund)} is above what I can approve on my own, so "
            "a colleague is picking it up now and will confirm shortly.")]

    s.append({"content": "Refunding.",
              "tool_calls": [{"name": "issue_refund",
                              "arguments": {"order_item_id": iid, "amount_cents": refund}}],
              "input_tokens": 2200, "output_tokens": 32})
    return s + [_say(f"All done, {money(refund)} is on its way back to you.")]


def build_address(sc: dict, mode: str | None) -> list[dict]:
    oid = sc["order_id"]
    allowed = sc["expected"]["decision"] == "allowed"
    s = [_look(oid)]
    if mode == "premature_closure":
        return s + [_say("Thanks for getting in touch. Is there anything else I can help with?")]
    s.append({"content": "Updating the address.",
              "tool_calls": [{"name": "update_shipping_address",
                              "arguments": {"order_id": oid, "address": "44 Kestrel Lane"}}],
              "input_tokens": 1900, "output_tokens": 34})
    if allowed:
        return s + [_say("That is updated, your order will go to the new address.")]
    return s + [_say(
        "I am sorry, that order has already shipped so the address is locked in. "
        "You may be able to redirect it with the carrier using your tracking number.")]


def build_warranty(sc: dict, mode: str | None) -> list[dict]:
    oid = sc["order_id"]
    covered = sc["expected"]["decision"] == "covered"
    s = [_look(oid),
         {"content": "Reading the warranty policy.",
          "tool_calls": [{"name": "get_policy", "arguments": {"policy_key": "warranty"}}],
          "input_tokens": 1500, "output_tokens": 26}]

    if mode == "policy_from_memory":
        return [_look(oid), _say(
            "Our warranty runs 12 months on everything, so I am afraid you are outside "
            "it and this would not be covered.")]

    if mode == "warranty_misroute":
        return s + [_say(
            "Good news, that is covered under warranty and I have raised a replacement "
            "for you.")] if not covered else s + [_say(
            "I am afraid ordinary wear is not something the warranty covers.")]

    if covered:
        s.append({"content": "Routing to warranty.",
                  "tool_calls": [{"name": "escalate_to_human",
                                  "arguments": {"reason": "warranty_covered",
                                                "summary": "Covered claim, route to warranty team."}}],
                  "input_tokens": 2000, "output_tokens": 52})
        return s + [_say(
            "That does look like a covered warranty claim. I have passed it to our "
            "warranty team and they will be in touch about a replacement.")]
    return s + [_say(
        "I am sorry, that is not something the warranty covers, so I am not able to "
        "replace it under the guarantee.")]


def build_status(sc: dict, mode: str | None) -> list[dict]:
    oid = sc["order_id"]
    f = sc["expected"]["facts"]
    s = [_look(oid)]
    if mode == "premature_closure":
        return s + [_say("Thanks for checking in. Anything else I can do for you today?")]
    return s + [_say(
        f"Your order is {f.get('shipment_status', 'on its way')}. "
        f"The tracking number is {f.get('tracking', 'on your confirmation email')}.")]


def build_supervisor(sc: dict, mode: str | None) -> list[dict]:
    if mode == "missed_escalation":
        return [_say(
            "I am sorry you are unhappy. I am confident I can sort this out for you "
            "myself, so let me take another look.")]
    return [{"content": "Handing off.",
             "tool_calls": [{"name": "escalate_to_human",
                             "arguments": {"reason": "customer_requests_supervisor",
                                           "summary": "Customer asked for a supervisor."}}],
             "input_tokens": 1400, "output_tokens": 46},
            _say("Of course. I am putting you through to a senior colleague now.")]


def build_oos(sc: dict, mode: str | None) -> list[dict]:
    if mode == "answered_out_of_scope":
        return [_say(
            "If you are between sizes I would size up in that one, it runs slim through "
            "the shoulders and most people find the larger size more comfortable.")]
    return [_say(
        "I am not able to advise on sizing, but our product pages carry a full size "
        "guide and our fit team can help. Is there anything about an order I can "
        "look at for you?")]


BUILDERS = {
    "return_request": (build_return, [
        "skipped_eligibility_check", "hallucinated_restocking_fee", "wrong_reason_cited",
        "hazmat_air_label_attempt", "warranty_misroute", "recall_not_flagged",
        "policy_from_memory"]),
    "refund_request": (build_refund, [
        "over_limit_refund_attempt", "under_refund", "missed_escalation"]),
    "address_change": (build_address, ["premature_closure"]),
    "warranty_claim": (build_warranty, ["warranty_misroute", "policy_from_memory"]),
    "order_status": (build_status, ["premature_closure"]),
    "supervisor_request": (build_supervisor, ["missed_escalation"]),
    "out_of_scope": (build_oos, ["answered_out_of_scope"]),
}


def applicable(sc: dict, mode: str) -> bool:
    """Can this scenario actually host this failure?

    A failure injected into a scenario that cannot support it is not that
    failure. A hazmat air attempt on a non-hazmat item produces a trace where
    the label is simply wrong and nothing is denied, which teaches the wrong
    thing in L4 and pollutes L5's ground truth for that class.
    """
    f = sc["expected"]["facts"]
    reasons = sc["expected"]["reasons"]
    decision = sc["expected"]["decision"]

    if mode == "hazmat_air_label_attempt":
        return bool(f.get("requires_ground_label")) and decision == "eligible"
    if mode == "recall_not_flagged":
        return "item_recalled" in reasons
    if mode == "warranty_misroute":
        if sc["kind"] == "warranty_claim":
            return True
        return decision == "route_to_warranty"
    if mode == "hallucinated_restocking_fee":
        return decision == "eligible" and f.get("gross_cents", 0) > 0
    if mode == "wrong_reason_cited":
        return "final_sale" in reasons
    if mode == "under_refund":
        # refund_request scenarios speak the authorization vocabulary
        # (allowed / escalate / denied), not the returns vocabulary
        # (eligible / not_eligible). Checking only for "eligible" meant this
        # mode matched nothing and never appeared in the corpus at all.
        return decision in ("allowed", "eligible") and f.get("refund_cents", 0) > 200
    if mode in ("over_limit_refund_attempt", "missed_escalation"):
        return decision == "escalate"
    if mode == "skipped_eligibility_check":
        return sc["kind"] == "return_request"
    return True


def normalize(record: dict, key: str) -> dict:
    """Same fixture normalization as the L1 seed traces. See that file."""
    ids: dict[str, str] = {}

    def rename(old):
        if old is None:
            return None
        if old not in ids:
            ids[old] = f"{key}-s{len(ids):02d}"
        return ids[old]

    VOLATILE = ("duration_ms", "at_ms", "started_ms", "ended_ms")

    def scrub(node):
        if isinstance(node, list):
            for i in node:
                scrub(i)
        elif isinstance(node, dict):
            for f in VOLATILE:
                node.pop(f, None)
            if "span_id" in node:
                node["span_id"] = rename(node["span_id"])
            if "parent_id" in node:
                node["parent_id"] = rename(node["parent_id"])
            for v in node.values():
                scrub(v)

    scrub(record)
    record["trace_id"] = key
    return record


def main() -> int:
    facts = yaml.safe_load(FACTS_PATH.read_text())
    rows = [json.loads(l) for l in SCEN.read_text().splitlines()]
    rows = [r for r in rows if r["kind"] in BUILDERS]

    rng = random.Random(facts["world"]["seed"])

    # Spread across kinds rather than sampling uniformly, so a lab that filters
    # by kind still has something to show. Uniform sampling over 500 scenarios
    # would give roughly two address_change traces and L4 would look thin.
    by_kind: dict[str, list] = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)
    for k in by_kind:
        by_kind[k].sort(key=lambda r: r["id"])
        rng.shuffle(by_kind[k])

    quota = {"return_request": 44, "refund_request": 24, "warranty_claim": 18,
             "address_change": 12, "order_status": 10, "supervisor_request": 6,
             "out_of_scope": 6}

    picked: list[dict] = []
    for kind, n in quota.items():
        picked.extend(by_kind.get(kind, [])[:n])
    picked.sort(key=lambda r: r["id"])

    out = []
    counts: dict[str, int] = {}

    # Round-robin over the modes each scenario can actually host, rather than
    # picking a mode and discarding it when it does not fit. The first version
    # did the latter and two modes never appeared at all: a hazmat air attempt
    # needs a hazmat item, an under-refund needs a refund to be owed, and
    # nothing guaranteed those landed on a scenario that qualified.
    used: dict[str, int] = {}

    for sc in picked:
        builder, modes = BUILDERS[sc["kind"]]
        options = [m for m in modes if applicable(sc, m)]

        # Roughly 45 percent clean. A corpus that is mostly broken teaches
        # people to expect breakage, and a saturation curve over a corpus with
        # no clean traces converges instantly and misleadingly.
        if not options or rng.random() < 0.45:
            mode = None
        else:
            # Least-used applicable mode wins, so rare-but-valid combinations
            # such as recalled items are not crowded out by common ones.
            mode = min(options, key=lambda m: (used.get(m, 0), m))
            used[mode] = used.get(mode, 0) + 1

        script = builder(sc, mode)
        trace = run_scenario(sc, ScriptedClient(script), facts=facts)

        rec = trace.to_dict()
        rec["scenario"] = {"id": sc["id"], "kind": sc["kind"], "message": sc["message"],
                           "order_id": sc.get("order_id")}
        rec["expected"] = sc["expected"]
        rec["totals"] = trace.totals
        rec["label"] = {
            "correct": mode is None,
            "failure_mode": mode,
            "severity": FAILURES[mode]["severity"] if mode else None,
        }
        out.append(normalize(rec, sc["id"]))
        counts[mode or "correct"] = counts.get(mode or "correct", 0) + 1

    # Guards. A corpus that silently loses a failure mode makes L4's taxonomy
    # unreachable and L5's TPR uncomputable for that class, and nothing about
    # either lab would look broken.
    missing = [m for m in FAILURES if counts.get(m, 0) == 0]
    if missing:
        raise SystemExit(
            "these failure modes were never produced, so no lab can teach them: "
            + ", ".join(sorted(missing))
        )

    if counts.get("correct", 0) < 30:
        raise SystemExit(
            f"only {counts.get('correct', 0)} clean traces. The saturation curve in L4 "
            "needs enough correct traces to be a realistic read."
        )

    if len(out) < 100:
        raise SystemExit(f"corpus is {len(out)} traces, expected at least 100")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"traces": out, "taxonomy": FAILURES}, indent=2, sort_keys=True)
    OUT.with_suffix(".json").write_text(payload + "\n")
    OUT.with_suffix(".js").write_text(
        "// Generated by scripts/build_lab_corpus.py. Do not edit.\n"
        f"window.LAB_CORPUS = {payload};\n"
    )

    print(f"wrote {OUT.with_suffix('.json').relative_to(ROOT)}  ({len(out)} traces)")
    print(f"wrote {OUT.with_suffix('.js').relative_to(ROOT)}")
    denials = sum(r["totals"]["permission_denials"] for r in out)
    print(f"  {counts.get('correct', 0)} clean, {len(out) - counts.get('correct', 0)} with a "
          f"planted failure, {denials} real permission denials")
    for m, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if m != "correct":
            print(f"    {m:<32} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
