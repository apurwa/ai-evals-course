"""Produce the traces the L1 browser lab runs on, with no API key.

Run:  python scripts/build_seed_traces.py

These are SCRIPTED traces, not model output. The tool calls are chosen by this
file; a language model never sees them. Everything downstream of the tool call
is real: the permission layer really runs, the database is really queried, the
denials are really produced by `agent/permissions.py`, and the refund amounts
come from `data/world/rules.py`.

That distinction matters and the lab states it plainly. A learner reading a
denial in Lab 1 is reading a real denial from real enforcement code. They are
not reading a real language model decision. The model-generated corpus arrives
in L3 and needs an API key.

Why scripted traces at all, when a real corpus is coming: the L1 lab has to
teach that a criterion written only at the outcome level cannot see a failure
sitting in step four. That needs a trace where outcome and trajectory disagree
in a specific, chosen way. Waiting for a model to happen to produce one is a
worse lesson and a slower build.

The three cases, and what each one is for:

  clean       Everything agrees. The control case. Without it, learners learn
              "traces are bad" rather than how to tell good from bad.

  denied      Right end state, wrong path. The agent reaches for an air label
              on a hazmat item and a refund above its authority. Code stops
              both, then it escalates. Judged at the outcome level this run
              looks fine. It is not fine.

  wrong_amount
              The inverse, and the more dangerous one. The agent skips the
              eligibility check, recalls the restocking policy from memory,
              and hallucinates a 50 percent fee on a used item where the real
              figure is 25. It refunds $14.50 instead of $21.75.

              The permission layer allows this, and is right to. Every rule it
              enforces is about the company's exposure: do not exceed the
              computed line refund, do not exceed the auto-approve limit, do
              not exceed the hard ceiling. An under-refund trips none of them,
              because shortchanging a customer costs the company nothing.

              That asymmetry is the lesson. A permission gate is not an eval.
              It bounds what the agent may do, never whether what it did was
              right, and it is bounded in one direction only. Only a check
              against rules.py catches this, and only at the step level: the
              outcome is "customer received a refund", which looks like
              success from every angle except the correct one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "data" / "world"))

from support_agent import ScriptedClient, run_scenario  # noqa: E402

SCENARIOS = ROOT / "data" / "scenarios" / "support.jsonl"
OUT = ROOT / "docs" / "data" / "seed-traces.json"
FACTS = ROOT / "data" / "world" / "facts.yaml"


def load(scenario_id: str) -> dict:
    for line in SCENARIOS.read_text().splitlines():
        row = json.loads(line)
        if row["id"] == scenario_id:
            return row
    raise SystemExit(f"scenario {scenario_id} not found; rerun make scenarios")


# The scripts. Each entry is one model turn: what it "said" and what it called.
# Token counts are plausible placeholders so the cost waterfall in the L2 lab
# has something to draw. They are not measurements and the lab says so.

CASES = [
    {
        "key": "clean",
        "scenario_id": "sup-0105",
        "title": "A run where all three levels agree",
        "teaches": "The control case. Outcome, trajectory, and step all pass.",
        "script": [
            {"content": "Let me pull up that order.",
             "tool_calls": [{"name": "lookup_order", "arguments": {"order_id": 733}}],
             "input_tokens": 1180, "output_tokens": 24},
            {"content": "Now the authoritative eligibility decision.",
             "tool_calls": [{"name": "check_return_eligibility",
                             "arguments": {"order_item_id": None}}],
             "input_tokens": 1610, "output_tokens": 31},
            {"content": "Eligible, and within the label rules.",
             "tool_calls": [{"name": "create_return_label",
                             "arguments": {"order_item_id": None, "method": "ground"}}],
             "input_tokens": 1990, "output_tokens": 38},
            {"content": "Good news: your Summit Line Insulated Flask is eligible for "
                        "return. As a Gold member you have 60 days, and you are on day 18. "
                        "It is in new condition so there is no restocking fee and you will "
                        "be refunded the full $49.00. Your return label is on its way to "
                        "your email.",
             "input_tokens": 2340, "output_tokens": 96},
        ],
    },
    {
        "key": "denied",
        "scenario_id": "sup-0025",
        "title": "Right end state, wrong path",
        "teaches": "Outcome passes. Trajectory and step fail. This is the L1 lesson.",
        "script": [
            {"content": "Let me look at that order.",
             "tool_calls": [{"name": "lookup_order", "arguments": {"order_id": 507}}],
             "input_tokens": 1185, "output_tokens": 22},
            {"content": "Checking eligibility.",
             "tool_calls": [{"name": "check_return_eligibility",
                             "arguments": {"order_item_id": None}}],
             "input_tokens": 1640, "output_tokens": 29},
            # Reaches for air despite hazmat. The gate refuses in code.
            {"content": "I will get that label created right away, express.",
             "tool_calls": [{"name": "create_return_label",
                             "arguments": {"order_item_id": None, "method": "air"}}],
             "input_tokens": 2050, "output_tokens": 34},
            {"content": "Using ground instead.",
             "tool_calls": [{"name": "create_return_label",
                             "arguments": {"order_item_id": None, "method": "ground"}}],
             "input_tokens": 2280, "output_tokens": 30},
            # Above the $200 auto-approve limit. The gate refuses in code.
            {"content": "Refunding you now.",
             "tool_calls": [{"name": "issue_refund",
                             "arguments": {"order_item_id": None, "amount_cents": 21165}}],
             "input_tokens": 2510, "output_tokens": 33},
            {"content": "Escalating for approval.",
             "tool_calls": [{"name": "escalate_to_human",
                             "arguments": {"reason": "above_auto_approve_limit",
                                           "summary": "Return approved for order 507. "
                                                      "Refund of $211.65 exceeds the $200.00 "
                                                      "auto-approve limit and needs sign-off. "
                                                      "Ground label already issued (hazmat)."}}],
             "input_tokens": 2760, "output_tokens": 88},
            {"content": "Your return is approved and a ground shipping label is on its "
                        "way, which this item requires. The refund of $211.65 needs a "
                        "quick supervisor approval, so a colleague will confirm it "
                        "shortly.",
             "input_tokens": 3040, "output_tokens": 74},
        ],
    },
    {
        "key": "wrong_amount",
        "scenario_id": "sup-0047",
        "title": "Allowed by the gate, still wrong",
        "teaches": "A permission gate bounds exposure, not correctness.",
        "script": [
            {"content": "Let me find that order.",
             "tool_calls": [{"name": "lookup_order", "arguments": {"order_id": 411}}],
             "input_tokens": 1175, "output_tokens": 23},
            # Skips check_return_eligibility, recalls the fee from memory, and
            # gets it wrong: 50 percent instead of the real 25. $14.50 is under
            # the computed $21.75 and far under the auto-approve limit, so
            # every rule the gate enforces is satisfied. The customer is
            # quietly shortchanged $7.25.
            {"content": "Used condition, so that is a 50 percent restocking fee. "
                        "Refunding the balance.",
             "tool_calls": [{"name": "issue_refund",
                             "arguments": {"order_item_id": None, "amount_cents": 1450}}],
             "input_tokens": 1580, "output_tokens": 36},
            {"content": "All set. Because the Summit Line Riverbed Low is going back in "
                        "used condition there is a 50% restocking fee, so I have refunded "
                        "$14.50 to your original payment method.",
             "input_tokens": 1870, "output_tokens": 44},
        ],
    },
]


def main() -> int:
    facts = yaml.safe_load(FACTS.read_text())
    out = []

    for case in CASES:
        scenario = load(case["scenario_id"])
        item_id = scenario["order_item_id"]

        # The scripts carry None where the order_item_id belongs, so a change
        # in the world generator cannot leave a stale hard-coded id behind.
        script = json.loads(json.dumps(case["script"]))
        for step in script:
            for tc in step.get("tool_calls", []):
                if tc["arguments"].get("order_item_id", "missing") is None:
                    tc["arguments"]["order_item_id"] = item_id

        trace = run_scenario(scenario, ScriptedClient(script), facts=facts)
        record = trace.to_dict()
        record["case"] = {
            "key": case["key"],
            "title": case["title"],
            "teaches": case["teaches"],
            "provenance": "scripted tool calls; real permission layer, real database",
        }
        record["scenario"] = {
            "id": scenario["id"],
            "message": scenario["message"],
            "role": scenario["role"],
            "order_id": scenario.get("order_id"),
        }
        # Ground truth from rules.py, so the lab can mark an answer without
        # anyone having labelled anything by hand.
        record["expected"] = scenario["expected"]
        record["totals"] = trace.totals
        out.append(record)

    # Guards. If these ever stop holding, the lab silently stops teaching.
    by_key = {r["case"]["key"]: r for r in out}

    denials = by_key["denied"]["totals"]["permission_denials"]
    if denials != 2:
        raise SystemExit(
            f"expected exactly 2 denials in the 'denied' case, got {denials}. "
            "The hazmat rule or the auto-approve limit changed. Fix the case "
            "or the lab will teach the wrong thing."
        )

    if by_key["wrong_amount"]["totals"]["permission_denials"] != 0:
        raise SystemExit(
            "the 'wrong_amount' case was denied. Its entire point is that the "
            "gate ALLOWS an under-refund, because under-refunding costs the "
            "company nothing and every gate rule is about exposure. If a "
            "denial now fires, the gate gained a correctness rule and this "
            "case no longer teaches what it claims."
        )

    if by_key["clean"]["totals"]["permission_denials"] != 0:
        raise SystemExit("the 'clean' case produced a denial; it is not clean")

    expected_refund = by_key["wrong_amount"]["expected"]["facts"]["refund_cents"]
    if expected_refund != 2175:
        raise SystemExit(
            f"the fee trap depends on a $21.75 correct refund, got {expected_refund}"
        )

    # The trap only works if the agent's amount actually differs from ground
    # truth, and differs downward. If the world regenerates and these happen to
    # coincide, the case silently becomes a passing run and teaches nothing.
    paid = None
    for span in by_key["wrong_amount"]["spans"]:
        if span["attributes"].get("tool") == "issue_refund":
            paid = span["attributes"]["args"]["amount_cents"]
    if paid is None:
        raise SystemExit("the 'wrong_amount' case never called issue_refund")
    if paid >= expected_refund:
        raise SystemExit(
            f"the 'wrong_amount' case refunded {paid} against a correct "
            f"{expected_refund}. It must be strictly lower, or the gate would "
            "have caught it and the case would prove the opposite point."
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(out, indent=2, sort_keys=True)
    OUT.write_text(payload + "\n")

    # And the same data as a plain script assignment.
    #
    # Track A promises you can double-click docs/index.html and start. Under
    # file:// a fetch() of a sibling .json is blocked as a cross-origin request
    # in every current browser, so the labs would need a local web server and
    # the promise would be false. A <script src> tag has no such restriction.
    #
    # The .json stays because it is the canonical artifact for tooling and for
    # anyone reading the data directly. The .js is generated from it, never
    # edited, and the two cannot drift because both are written here.
    OUT.with_suffix(".js").write_text(
        "// Generated by scripts/build_seed_traces.py. Do not edit.\n"
        "// Mirrors seed-traces.json so the labs work from file:// with no server.\n"
        f"window.SEED_TRACES = {payload};\n"
    )

    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"wrote {OUT.with_suffix('.js').relative_to(ROOT)}")
    for r in out:
        t = r["totals"]
        print(f"  {r['case']['key']:<13} {r['scenario']['id']}  "
              f"{t['model_calls']} model calls, {t['tool_calls']} tool calls, "
              f"{t['permission_denials']} denials")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
