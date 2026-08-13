"""The Wayfarer support agent, with an explicit control loop.

Why the loop is written out rather than delegated to an agent framework:

  * L1 lists "tools, context, and the control loop" as the building blocks to
    learn. A framework that hides the loop hides one third of the lesson.
  * L2 needs a span per model call carrying its own tokens, cost, and latency.
    Getting that from inside a framework's abstraction is possible but
    indirect, and the indirection is not what the course is teaching.
  * L8 varies harness-level behavior (retries, reflection). That is far easier
    to demonstrate on a loop you can read in one screen.

The real course uses the OpenAI Agents SDK. Swapping this loop for it is a
reasonable exercise and changes nothing downstream, because everything after
this file consumes traces, not agent objects.

Run:
    python agent/support_agent.py --selftest         # no API key needed
    python agent/support_agent.py --scenario sup-0001
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Protocol

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "data" / "world"))

from langfuse_sink import get_sink  # noqa: E402
from toolset import Session, ToolRegistry  # noqa: E402
from tracing import ModelPricing, Trace, prompt_hash  # noqa: E402

FACTS_PATH = ROOT / "data" / "world" / "facts.yaml"
PRICING_PATH = ROOT / "evals" / "pricing.json"

DEFAULT_MODEL = os.environ.get("AGENT_MODEL", "gpt-4.1-mini")
MAX_STEPS = 8


SYSTEM_PROMPT = """\
You are a customer support agent for Wayfarer Supply Co., an outdoor and \
expedition gear retailer. You are handling one customer conversation.

How to work:

- Look things up before you answer. Never state an order detail, a policy, or a \
refund amount that you have not read from a tool result in this conversation.
- Read policy with get_policy. Do not recall policy from memory.
- Decide return questions with check_return_eligibility. It is authoritative. \
Do not compute a refund yourself, and do not argue with its decision.
- When you issue a refund, use exactly the refund_cents that \
check_return_eligibility returned. That figure already accounts for any \
restocking fee.
- Hazmat items must use method "ground" when creating a return label.
- Escalate with escalate_to_human when policy requires it: a refund above the \
auto-approve limit, a recalled item, a disputed warranty decision, a request \
for a supervisor, suspected fraud, or a request outside what you handle.
- You handle post-purchase support only. Sizing advice, product \
recommendations, price matching, and wholesale enquiries are out of scope. \
Decline politely and escalate if the customer needs a person.

How to write:

- Be brief and concrete. Lead with the answer.
- State policy plainly. Do not apologise for it repeatedly.
- Give the customer the next concrete step.
"""


# ---------------------------------------------------------------------------
# Model clients
# ---------------------------------------------------------------------------

class ModelClient(Protocol):
    def complete(self, messages: list[dict], tools: list[dict], model: str) -> dict:
        """Return {content, tool_calls, input_tokens, output_tokens, cached_tokens}."""


class OpenAIClient:
    def __init__(self) -> None:
        try:
            from openai import OpenAI
        except ImportError:  # pragma: no cover
            raise SystemExit("openai is not installed.  pip install -r requirements.txt")
        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("OPENAI_API_KEY is not set.")
        self._client = OpenAI()

    def complete(self, messages: list[dict], tools: list[dict], model: str) -> dict:
        resp = self._client.chat.completions.create(
            model=model, messages=messages, tools=tools or None, temperature=0,
        )
        choice = resp.choices[0].message
        usage = resp.usage
        cached = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0
        return {
            "content": choice.content,
            "tool_calls": [
                {"id": tc.id, "name": tc.function.name,
                 "arguments": json.loads(tc.function.arguments or "{}")}
                for tc in (choice.tool_calls or [])
            ],
            "input_tokens": usage.prompt_tokens,
            "output_tokens": usage.completion_tokens,
            "cached_tokens": cached,
            "raw_message": choice,
        }


class ScriptedClient:
    """A deterministic stand-in, so the plumbing is testable with no API key.

    This exists to verify the loop, the permission gate, and the trace shape,
    not to simulate a language model. It never appears in the real corpus run.
    """

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.calls = 0

    def complete(self, messages: list[dict], tools: list[dict], model: str) -> dict:
        step = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return {
            "content": step.get("content"),
            "tool_calls": [
                {"id": f"call_{i}", "name": tc["name"], "arguments": tc.get("arguments", {})}
                for i, tc in enumerate(step.get("tool_calls", []))
            ],
            "input_tokens": step.get("input_tokens", 1200),
            "output_tokens": step.get("output_tokens", 90),
            "cached_tokens": step.get("cached_tokens", 0),
            "raw_message": None,
        }


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

def load_pricing(model: str) -> ModelPricing:
    data = json.loads(PRICING_PATH.read_text())
    if not data.get("verified"):
        print(
            "warning: evals/pricing.json is not verified. Cost figures are "
            "estimates against unconfirmed prices. Verify before quoting them.",
            file=sys.stderr,
        )
    entry = data["models"].get(model)
    if entry is None:
        # An unpriced model must not silently cost zero. A zero-cost trace
        # quietly ruins the L9 cost analysis.
        print(f"warning: no pricing for {model!r}; costs will record as 0.0", file=sys.stderr)
        return ModelPricing(model, 0.0, 0.0)
    return ModelPricing(model, entry["input_per_m"], entry["output_per_m"],
                        entry.get("cached_input_per_m"))


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

def run_scenario(
    scenario: dict,
    client: ModelClient,
    *,
    model: str = DEFAULT_MODEL,
    facts: dict | None = None,
    max_steps: int = MAX_STEPS,
    sink: Any = None,
) -> Trace:
    facts = facts or yaml.safe_load(FACTS_PATH.read_text())
    pricing = load_pricing(model)
    role = scenario.get("role", "support")

    trace = Trace(
        scenario_id=scenario["id"], role=role, model=model,
        metadata={"kind": scenario.get("kind"), "sampling": scenario.get("sampling"),
                  "order_id": scenario.get("order_id"),
                  "order_item_id": scenario.get("order_item_id")},
        sink=sink,
    )
    # Build one sink and pass it in, rather than letting run_scenario make its
    # own: a client per scenario would mean a connection and an auth round trip
    # per scenario, which is a lot of overhead to observe a corpus run.
    if sink is not None:
        sink.set_input(trace, scenario["message"])
    session = Session(role, facts, trace)
    registry = ToolRegistry(session)
    tools = registry.schemas_for_role()

    system = SYSTEM_PROMPT
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": scenario["message"]},
    ]

    # One hash for the system prompt, recorded on every model call. This is
    # what makes "which prompt version produced this regression" answerable
    # months later, when the string itself has moved on.
    sys_hash = prompt_hash(system)
    final: str | None = None

    try:
        for step in range(max_steps):
            with trace.span(f"model_call:{step}", "model_call",
                            step=step, prompt_hash=sys_hash,
                            message_count=len(messages)) as span:
                out = client.complete(messages, tools, model)
                span.input_tokens = out["input_tokens"]
                span.output_tokens = out["output_tokens"]
                span.cached_tokens = out.get("cached_tokens", 0)
                span.cost_usd = pricing.cost(
                    span.input_tokens, span.output_tokens, span.cached_tokens)
                span.attributes["tool_calls_requested"] = [
                    tc["name"] for tc in out["tool_calls"]]

            if not out["tool_calls"]:
                final = out["content"] or ""
                break

            messages.append({
                "role": "assistant",
                "content": out["content"],
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"],
                                  "arguments": json.dumps(tc["arguments"])}}
                    for tc in out["tool_calls"]
                ],
            })

            for tc in out["tool_calls"]:
                result = registry.call(tc["name"], tc["arguments"])
                if tc["name"] == "escalate_to_human" and "error" not in result:
                    h = trace.open(f"handoff:{tc['arguments'].get('reason', '')}", "handoff",
                                   reason=tc["arguments"].get("reason"))
                    trace.close(h)
                messages.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": json.dumps(result, default=str),
                })
        else:
            # Exhausting the step budget is a real failure mode, not a crash.
            # It must be visible in the trace rather than looking like a short
            # conversation that simply ended.
            trace.spans[0].event("max_steps_exhausted", max_steps=max_steps)
            final = None

        trace.finish(final_response=final)
    except Exception as e:  # noqa: BLE001
        trace.finish(error=f"{type(e).__name__}: {e}")
        raise
    finally:
        session.close()

    return trace


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def selftest() -> int:
    """Exercise the loop, the gate, and the trace with no API key.

    Asserts the properties everything downstream relies on. If this passes, a
    real run differs only in what the model chooses to do.
    """
    facts = yaml.safe_load(FACTS_PATH.read_text())
    support = [json.loads(l) for l in (ROOT / "data" / "scenarios" / "support.jsonl").read_text().splitlines()]

    # Pick a real eligible line so the refund path is genuinely exercised.
    import sqlite3
    import rules
    conn = sqlite3.connect(ROOT / "data" / "world" / "wayfarer.db")
    item_id = next(
        i for i in (r[0] for r in conn.execute("SELECT id FROM order_items"))
        if rules.return_eligibility(conn, facts, i).decision == "eligible"
    )
    elig = rules.return_eligibility(conn, facts, item_id)
    good = elig.facts["refund_cents"]
    conn.close()

    scenario = dict(support[0], id="selftest-01", order_item_id=item_id, role="support")
    failures: list[str] = []

    # None unless LANGFUSE_TRACING=1, so the default selftest is untouched.
    # When it is set, these six runs are driven by ScriptedClient against the
    # real loop, gate and database, which means a full set of traces lands in
    # Langfuse without an API key and without spending anything. That includes
    # the denial and the step-exhaustion cases, which are the two most worth
    # looking at in a trace viewer and the two hardest to produce on demand.
    sink = get_sink()
    if sink is not None:
        print("langfuse: mirroring these runs to "
              f"{os.environ.get('LANGFUSE_HOST', 'http://localhost:3000')}")

    def check(label: str, ok: bool) -> None:
        print(f"  {'ok      ' if ok else 'FAILED  '} {label}")
        if not ok:
            failures.append(label)

    # 1. Happy path: look up, check, refund within limit.
    print("\nhappy path")
    t = run_scenario(scenario, ScriptedClient([
        {"tool_calls": [{"name": "check_return_eligibility",
                         "arguments": {"order_item_id": item_id}}]},
        {"tool_calls": [{"name": "issue_refund",
                         "arguments": {"order_item_id": item_id,
                                       "amount_cents": min(good, 20000)}}]},
        {"content": "Refund issued."},
    ]), facts=facts, model="gpt-4.1-mini", sink=sink)
    check("trace has a session root", t.spans[0].kind == "session")
    check("three model calls recorded", t.totals["model_calls"] == 3)
    check("two tool calls recorded", t.totals["tool_calls"] == 2)
    check("no denials on the happy path", t.totals["permission_denials"] == 0)
    check("tokens accumulated", t.totals["input_tokens"] > 0)
    check("cost is non-zero", t.totals["cost_usd"] > 0)
    check("final response captured", t.final_response == "Refund issued.")
    check("every span closed", all(s.ended_ms is not None for s in t.spans))
    check("prompt hash on model calls",
          all(s.attributes.get("prompt_hash") for s in t.spans if s.kind == "model_call"))
    check("serializes to json", isinstance(json.loads(t.to_json()), dict))

    # 2. The property L7 exists to demonstrate.
    print("\ninjection cannot beat the gate")
    t2 = run_scenario(dict(scenario, id="selftest-02"), ScriptedClient([
        {"tool_calls": [{"name": "issue_refund",
                         "arguments": {"order_item_id": item_id, "amount_cents": 500000}}]},
        {"content": "I could not process that."},
    ]), facts=facts, model="gpt-4.1-mini", sink=sink)
    denied = [s for s in t2.spans if s.status == "denied"]
    check("oversized refund denied", len(denied) == 1)
    check("denial recorded on the trace", t2.totals["permission_denials"] == 1)
    check("denial reason is the hard ceiling",
          denied[0].attributes.get("denied_reason") == "above_hard_ceiling")

    # 3. Role boundary.
    print("\nrole boundary")
    t3 = run_scenario(dict(scenario, id="selftest-03", role="analyst"), ScriptedClient([
        {"tool_calls": [{"name": "issue_refund",
                         "arguments": {"order_item_id": item_id, "amount_cents": 100}}]},
        {"content": "Not permitted."},
    ]), facts=facts, model="gpt-4.1-mini", sink=sink)
    check("analyst refund denied", t3.totals["permission_denials"] == 1)
    reg_role = [s for s in t3.spans if s.status == "denied"][0].attributes.get("denied_reason")
    check("denied for role, not amount", reg_role == "role_not_permitted")

    # 4. Hazmat method, derived from the database rather than the model.
    print("\nhazmat label method")
    conn = sqlite3.connect(ROOT / "data" / "world" / "wayfarer.db")
    haz = conn.execute("""
        SELECT oi.id FROM order_items oi JOIN products p ON p.id = oi.product_id
        WHERE p.is_hazmat = 1 LIMIT 1""").fetchone()[0]
    conn.close()
    t4 = run_scenario(dict(scenario, id="selftest-04"), ScriptedClient([
        {"tool_calls": [{"name": "create_return_label",
                         "arguments": {"order_item_id": haz, "method": "air"}}]},
        {"content": "done"},
    ]), facts=facts, model="gpt-4.1-mini", sink=sink)
    check("air label on hazmat denied", t4.totals["permission_denials"] == 1)

    # 5. Progressive tool disclosure.
    print("\ntool disclosure")
    conn = sqlite3.connect(ROOT / "data" / "world" / "wayfarer.db")
    tr = Trace(scenario_id="x", role="analyst", model="m")
    sess = Session("analyst", facts, tr)
    names = {s["function"]["name"] for s in ToolRegistry(sess).schemas_for_role()}
    sess.close()
    check("analyst is not offered issue_refund", "issue_refund" not in names)
    check("analyst is offered run_analytics_query", "run_analytics_query" in names)
    sess2 = Session("support", facts, Trace(scenario_id="y", role="support", model="m"))
    sup_names = {s["function"]["name"] for s in ToolRegistry(sess2).schemas_for_role()}
    sess2.close()
    conn.close()
    check("support is not offered run_analytics_query", "run_analytics_query" not in sup_names)
    check("support is offered issue_refund", "issue_refund" in sup_names)

    # 6. Step budget exhaustion is visible.
    print("\nstep budget")
    t6 = run_scenario(dict(scenario, id="selftest-06"), ScriptedClient([
        {"tool_calls": [{"name": "get_policy", "arguments": {"policy_key": "returns"}}]},
    ]), facts=facts, model="gpt-4.1-mini", max_steps=3, sink=sink)
    check("exhaustion recorded as an event",
          any(e.name == "max_steps_exhausted" for e in t6.spans[0].events))
    check("no final response when exhausted", t6.final_response is None)

    # Batched telemetry is lost if the process exits before the batch is sent,
    # which is the usual reason a short script shows nothing in the UI.
    if sink is not None:
        sink.flush()
        print(f"\nlangfuse: {sink.traces} traces flushed. "
              "Ingestion is queued, so give it a second, then open "
              f"{os.environ.get('LANGFUSE_HOST', 'http://localhost:3000')}")

    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("agent selftest passed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Wayfarer support agent")
    ap.add_argument("--selftest", action="store_true", help="run without an API key")
    ap.add_argument("--scenario", help="scenario id from data/scenarios/")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if not args.scenario:
        ap.error("pass --scenario ID or --selftest")

    rows = []
    for name in ("support", "analyst"):
        p = ROOT / "data" / "scenarios" / f"{name}.jsonl"
        rows += [json.loads(l) for l in p.read_text().splitlines()]
    scenario = next((r for r in rows if r["id"] == args.scenario), None)
    if scenario is None:
        raise SystemExit(f"no scenario {args.scenario!r}")

    trace = run_scenario(scenario, OpenAIClient(), model=args.model)
    print(json.dumps(trace.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
