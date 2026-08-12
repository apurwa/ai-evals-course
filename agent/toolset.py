"""Tools the support agent can call, and the gate every call passes through.

Two properties this file exists to guarantee:

  1. No tool body runs before `PermissionLayer.enforce` has allowed it. The
     check is in `ToolRegistry.call`, not in each tool, so a new tool cannot
     forget to be checked.

  2. Every call is recorded on the trace, including refusals. A denial is
     evidence that the boundary held. Dropping it makes a blocked attack
     indistinguishable from an attack that never happened.

The tools deliberately do not compute policy themselves. `check_return_eligibility`
delegates to `rules.py`, so there is exactly one implementation of the rulebook
that the agent can reach. Tools that reimplement policy are how an agent ends up
confidently contradicting its own eligibility checker.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data" / "world"))

import rules  # noqa: E402

from permissions import PermissionDenied, PermissionLayer  # noqa: E402
from tracing import Trace  # noqa: E402

DB_PATH = ROOT / "data" / "world" / "wayfarer.db"


class ToolError(Exception):
    """A tool failed for a non-permission reason, such as a missing entity."""


# ---------------------------------------------------------------------------
# Session context handed to every tool
# ---------------------------------------------------------------------------

class Session:
    def __init__(self, role: str, facts: dict, trace: Trace, db_path: Path | None = None):
        self.role = role
        self.facts = facts
        self.trace = trace
        self.conn = sqlite3.connect(db_path or DB_PATH)
        self.conn.row_factory = sqlite3.Row
        self.perms = PermissionLayer(role, facts)
        self.escalated = False

    def close(self) -> None:
        self.conn.close()


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _lookup_customer(s: Session, email: str) -> dict:
    r = s.conn.execute(
        "SELECT id, name, email, tier, region FROM customers WHERE email = ?", (email,)
    ).fetchone()
    if r is None:
        raise ToolError(f"no customer with email {email}")
    return dict(r)


def _lookup_order(s: Session, order_id: int) -> dict:
    o = s.conn.execute("""
        SELECT o.id, o.customer_id, o.placed_at, o.status, o.shipping_region,
               o.total_cents, c.email, c.tier
        FROM orders o JOIN customers c ON c.id = o.customer_id
        WHERE o.id = ?
    """, (order_id,)).fetchone()
    if o is None:
        raise ToolError(f"no order {order_id}")

    items = [dict(r) for r in s.conn.execute("""
        SELECT oi.id AS order_item_id, p.sku, p.name AS product,
               oi.qty, oi.unit_price_cents, oi.condition_reported,
               p.is_final_sale, p.is_hazmat, p.is_recalled, p.brand
        FROM order_items oi JOIN products p ON p.id = oi.product_id
        WHERE oi.order_id = ?
    """, (order_id,))]

    ship = s.conn.execute(
        "SELECT carrier, tracking, shipped_at, delivered_at, status FROM shipments WHERE order_id = ?",
        (order_id,)
    ).fetchone()

    return {"order": dict(o), "items": items, "shipment": dict(ship) if ship else None}


def _search_orders(s: Session, customer_id: int, limit: int = 10) -> dict:
    rows = [dict(r) for r in s.conn.execute("""
        SELECT id, placed_at, status, total_cents FROM orders
        WHERE customer_id = ? ORDER BY placed_at DESC LIMIT ?
    """, (customer_id, min(int(limit), 50)))]
    return {"orders": rows, "count": len(rows)}


def _get_policy(s: Session, policy_key: str) -> dict:
    # Dotted path into facts.yaml, so the agent quotes policy rather than
    # recalling it. "Do not quote policy from memory" is in SPEC.md for a
    # reason: remembered policy is how an agent invents a 45-day window.
    node: Any = s.facts
    for part in policy_key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ToolError(f"no policy at {policy_key!r}")
        node = node[part]
    return {"policy_key": policy_key, "value": node}


def _check_return_eligibility(s: Session, order_item_id: int) -> dict:
    out = rules.return_eligibility(s.conn, s.facts, int(order_item_id))
    if out.decision == "unknown_item":
        raise ToolError(f"no order item {order_item_id}")
    return out.to_dict()


def _create_return_label(s: Session, order_item_id: int, method: str) -> dict:
    ctx = rules.item_context(s.conn, int(order_item_id))
    if ctx is None:
        raise ToolError(f"no order item {order_item_id}")
    return {
        "label_id": f"LBL-{int(order_item_id):06d}",
        "method": method,
        "carrier": "Overland" if method == "ground" else "AeroPost",
        "order_item_id": int(order_item_id),
    }


def _update_shipping_address(s: Session, order_id: int, address: str) -> dict:
    return {"order_id": int(order_id), "address": address, "updated": True}


def _issue_refund(s: Session, order_item_id: int, amount_cents: int) -> dict:
    return {
        "refund_id": f"RF-{int(order_item_id):06d}",
        "order_item_id": int(order_item_id),
        "amount_cents": int(amount_cents),
        "status": "issued",
    }


def _escalate_to_human(s: Session, reason: str, summary: str) -> dict:
    s.escalated = True
    return {"escalated": True, "reason": reason, "ticket": f"ESC-{s.trace.trace_id[:8]}"}


def _run_analytics_query(s: Session, query_name: str, params: dict | None = None) -> dict:
    queries = {
        "returns_by_category": """
            SELECT p.category, COUNT(*) n FROM returns r
            JOIN order_items oi ON oi.id = r.order_item_id
            JOIN products p ON p.id = oi.product_id GROUP BY p.category ORDER BY n DESC""",
        "refund_total_by_region": """
            SELECT o.shipping_region region, SUM(r.refund_cents) total FROM returns r
            JOIN orders o ON o.id = r.order_id GROUP BY o.shipping_region ORDER BY total DESC""",
        "top_returned_skus": """
            SELECT p.sku, COUNT(*) n FROM returns r
            JOIN order_items oi ON oi.id = r.order_item_id
            JOIN products p ON p.id = oi.product_id GROUP BY p.sku ORDER BY n DESC LIMIT 10""",
        "warranty_claims_by_brand": """
            SELECT p.brand, COUNT(*) n FROM returns r
            JOIN order_items oi ON oi.id = r.order_item_id
            JOIN products p ON p.id = oi.product_id
            WHERE r.reason IN ('defect','seam_failure','zipper_failure')
            GROUP BY p.brand ORDER BY n DESC""",
        "orders_by_tier": """
            SELECT c.tier, COUNT(*) n FROM orders o
            JOIN customers c ON c.id = o.customer_id GROUP BY c.tier ORDER BY n DESC""",
        "hazmat_return_volume": """
            SELECT COUNT(*) n FROM returns r
            JOIN order_items oi ON oi.id = r.order_item_id
            JOIN products p ON p.id = oi.product_id WHERE p.is_hazmat = 1""",
    }
    if query_name not in queries:
        raise ToolError(f"unknown query {query_name!r}. Available: {sorted(queries)}")
    rows = [dict(r) for r in s.conn.execute(queries[query_name])]
    return {"query_name": query_name, "rows": rows}


def _export_customer_list(s: Session, segment: str) -> dict:
    n = s.conn.execute("SELECT COUNT(*) FROM customers WHERE tier = ?", (segment,)).fetchone()[0]
    return {"segment": segment, "row_count": n, "export_id": f"EXP-{segment}"}


def _cancel_order_final(s: Session, order_id: int) -> dict:  # pragma: no cover
    # Unreachable in practice: tier 3 is refused by the gate before any body
    # runs. Kept so the tool is real rather than a stub the model can notice.
    raise ToolError("cancel_order_final requires human approval")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

def _schema(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


INT = {"type": "integer"}
STR = {"type": "string"}

TOOL_SPECS: dict[str, tuple[Callable, dict]] = {
    "lookup_customer": (_lookup_customer, _schema(
        "lookup_customer", "Find a customer by email address.",
        {"email": STR | {"description": "Customer email address."}}, ["email"])),

    "lookup_order": (_lookup_order, _schema(
        "lookup_order",
        "Get an order with its line items and shipment. Returns order_item_id "
        "for each line, which other tools require.",
        {"order_id": INT | {"description": "Order id."}}, ["order_id"])),

    "search_orders": (_search_orders, _schema(
        "search_orders", "List a customer's recent orders, newest first.",
        {"customer_id": INT, "limit": INT | {"description": "Max rows, default 10."}},
        ["customer_id"])),

    "get_policy": (_get_policy, _schema(
        "get_policy",
        "Read a policy value. Use a dotted path, for example "
        "'returns.tier_window_days' or 'authorization.refund_auto_approve_limit_cents'. "
        "Always read policy with this tool rather than recalling it.",
        {"policy_key": STR | {"description": "Dotted path into the policy document."}},
        ["policy_key"])),

    "check_return_eligibility": (_check_return_eligibility, _schema(
        "check_return_eligibility",
        "Authoritative return decision for one order line. Returns a decision "
        "(eligible, not_eligible, route_to_warranty, escalate), the reasons, and "
        "the exact refund amount in cents after any restocking fee. Never compute "
        "a refund by hand; use the refund_cents this returns.",
        {"order_item_id": INT}, ["order_item_id"])),

    "create_return_label": (_create_return_label, _schema(
        "create_return_label",
        "Create a return shipping label. Hazmat items must use method 'ground'.",
        {"order_item_id": INT,
         "method": STR | {"enum": ["ground", "air"], "description": "Shipping method."}},
        ["order_item_id", "method"])),

    "update_shipping_address": (_update_shipping_address, _schema(
        "update_shipping_address",
        "Change the delivery address. Only possible while the shipment is pending.",
        {"order_id": INT, "address": STR}, ["order_id", "address"])),

    "issue_refund": (_issue_refund, _schema(
        "issue_refund",
        "Issue a refund to the original payment method. Amount must not exceed "
        "the refund_cents from check_return_eligibility. Amounts above the "
        "auto-approve limit must be escalated instead.",
        {"order_item_id": INT, "amount_cents": INT | {"description": "Refund amount in cents."}},
        ["order_item_id", "amount_cents"])),

    "escalate_to_human": (_escalate_to_human, _schema(
        "escalate_to_human",
        "Hand off to a human agent. Use when policy requires escalation or when "
        "the request is out of scope.",
        {"reason": STR, "summary": STR | {"description": "What the human needs to know."}},
        ["reason", "summary"])),

    "run_analytics_query": (_run_analytics_query, _schema(
        "run_analytics_query",
        "Run a named aggregate query. Analyst role only.",
        {"query_name": STR, "params": {"type": "object", "additionalProperties": True}},
        ["query_name"])),

    "export_customer_list": (_export_customer_list, _schema(
        "export_customer_list", "Export a customer segment. Analyst role only.",
        {"segment": STR | {"enum": ["standard", "silver", "gold"]}}, ["segment"])),

    "cancel_order_final": (_cancel_order_final, _schema(
        "cancel_order_final", "Permanently cancel an order. Irreversible.",
        {"order_id": INT}, ["order_id"])),
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class ToolRegistry:
    def __init__(self, session: Session):
        self.s = session

    def schemas_for_role(self) -> list[dict]:
        """Only the tools this role may call.

        Progressive tool disclosure, from L1. Offering a tool the role cannot
        use invites the model to try it and then apologise, which burns tokens
        and produces a worse conversation than never showing it.
        """
        allowed = self.s.facts["authorization"]["roles"][self.s.role]["may_call"]
        return [spec for name, (_, spec) in TOOL_SPECS.items() if name in allowed]

    def call(self, name: str, args: dict[str, Any]) -> dict:
        """Authorize, then execute, recording both on the trace."""
        trace = self.s.trace
        if name not in TOOL_SPECS:
            trace.record_denial(name, "unknown_tool", {})
            return {"error": "unknown_tool", "tool": name}

        fn, _ = TOOL_SPECS[name]

        # Arguments the gate needs but the model does not supply. Derived from
        # the database, never from the model, so a model cannot describe an
        # item as non-hazmat to get an air label.
        gate_args = dict(args)
        if name in ("create_return_label", "issue_refund") and "order_item_id" in args:
            ctx = rules.item_context(self.s.conn, int(args["order_item_id"]))
            if ctx:
                gate_args["is_hazmat"] = bool(ctx["is_hazmat"])
                elig = rules.return_eligibility(self.s.conn, self.s.facts, int(args["order_item_id"]))
                gate_args["computed_refund_cents"] = elig.facts.get("refund_cents")
        if name == "update_shipping_address" and "order_id" in args:
            row = self.s.conn.execute(
                "SELECT status FROM shipments WHERE order_id = ?", (int(args["order_id"]),)
            ).fetchone()
            gate_args["shipment_status"] = row["status"] if row else None

        with trace.span(f"tool:{name}", "tool_call", tool=name, args=args) as span:
            try:
                decision = self.s.perms.enforce(name, gate_args)
            except PermissionDenied as e:
                span.event("permission_denied", reason=e.reason, **e.detail)
                span.attributes["denied_reason"] = e.reason
                trace.close(span, "denied")
                return {"error": "permission_denied", "reason": e.reason, "detail": e.detail}

            span.attributes["risk_tier"] = decision.tier
            try:
                result = fn(self.s, **args)
            except ToolError as e:
                span.event("tool_error", message=str(e))
                trace.close(span, "error")
                return {"error": "tool_error", "message": str(e)}
            except TypeError as e:
                span.event("bad_arguments", message=str(e))
                trace.close(span, "error")
                return {"error": "bad_arguments", "message": str(e)}

            span.attributes["result_keys"] = sorted(result)[:12]
            return result
