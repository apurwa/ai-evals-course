"""Ground truth. Pure functions over the world database.

This is the most important file in the repository.

Every scenario's expected outcome is *computed* here, never written by hand and
never guessed by a model. That is the L3 discipline made mechanical: if a
scenario's expected answer cannot be derived from code or the database, the
scenario does not belong in the corpus.

Two consequences worth internalizing:

  * These functions define correctness for the whole course. A grader that
    disagrees with rules.py is wrong by definition, including a grader written
    by a very confident language model.
  * Nothing here reads the system clock. "Now" arrives as an argument, sourced
    from facts.yaml. A function here that called datetime.now() would make the
    corpus rot silently.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any


# ---------------------------------------------------------------------------
# Outcome container
# ---------------------------------------------------------------------------

@dataclass
class Outcome:
    """The computed correct answer for one question about the world.

    `decision` is the single word a grader compares against. `reasons` carries
    the machine-readable causes, which is what makes a failure mode countable
    later rather than merely describable.
    """

    decision: str
    reasons: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _row(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> sqlite3.Row | None:
    conn.row_factory = sqlite3.Row
    cur = conn.execute(sql, args)
    return cur.fetchone()


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _days_between(later: datetime, earlier: datetime) -> int:
    """Whole days elapsed. Floor, so a return at 29.9 days is still day 29."""
    return (later - earlier).days


# ---------------------------------------------------------------------------
# Item context
# ---------------------------------------------------------------------------

def item_context(conn: sqlite3.Connection, order_item_id: int) -> dict | None:
    """Everything the rules need about one order line, in a single read."""
    r = _row(conn, """
        SELECT
            oi.id            AS item_id,
            oi.order_id      AS order_id,
            oi.qty           AS qty,
            oi.unit_price_cents AS unit_price_cents,
            oi.condition_reported AS condition,
            p.id             AS product_id,
            p.sku            AS sku,
            p.name           AS product_name,
            p.brand          AS brand,
            p.category       AS category,
            p.is_final_sale  AS is_final_sale,
            p.is_hazmat      AS is_hazmat,
            p.warranty_months AS warranty_months,
            p.is_recalled    AS is_recalled,
            o.status         AS order_status,
            o.placed_at      AS placed_at,
            c.id             AS customer_id,
            c.name           AS customer_name,
            c.email          AS email,
            c.tier           AS tier,
            s.status         AS shipment_status,
            s.delivered_at   AS delivered_at,
            s.shipped_at     AS shipped_at
        FROM order_items oi
        JOIN orders   o ON o.id = oi.order_id
        JOIN products p ON p.id = oi.product_id
        JOIN customers c ON c.id = o.customer_id
        LEFT JOIN shipments s ON s.order_id = o.id
        WHERE oi.id = ?
    """, (order_item_id,))
    return dict(r) if r else None


# ---------------------------------------------------------------------------
# Returns
# ---------------------------------------------------------------------------

def return_eligibility(conn: sqlite3.Connection, facts: dict, order_item_id: int) -> Outcome:
    """Can this line be returned, and on what terms?

    Precedence is deliberate and is itself a teaching point. A recall outranks
    everything, because telling a customer to ship a recalled item back by the
    normal route is worse than refusing a valid return. Agents commonly get
    this ordering wrong: they check the return window first, find it expired,
    and never notice the recall.
    """
    ctx = item_context(conn, order_item_id)
    if ctx is None:
        return Outcome("unknown_item", ["no_such_order_item"])

    now = _parse(facts["world"]["now"])
    rules = facts["returns"]
    reasons: list[str] = []

    base_facts = {
        "sku": ctx["sku"],
        "tier": ctx["tier"],
        "condition": ctx["condition"],
        "order_status": ctx["order_status"],
        "gross_cents": ctx["unit_price_cents"] * ctx["qty"],
    }

    # 1. Recall outranks every other consideration.
    if ctx["is_recalled"]:
        return Outcome("escalate", ["item_recalled"], {
            **base_facts,
            "escalation_reason": "item_recalled",
            "must_not_issue_standard_label": True,
        })

    # 2. Nothing to return if it never arrived.
    if ctx["order_status"] != "delivered" or not ctx["delivered_at"]:
        return Outcome("not_eligible", [f"order_status_{ctx['order_status']}"], base_facts)

    delivered = _parse(ctx["delivered_at"])
    days_since = _days_between(now, delivered)
    window = rules["tier_window_days"][ctx["tier"]]
    base_facts |= {"days_since_delivery": days_since, "window_days": window}

    # 3. Damaged goods are a warranty matter, not a return, and carry no fee.
    if ctx["condition"] == "damaged":
        return Outcome("route_to_warranty", ["condition_damaged"], base_facts)

    # 4. Final sale is absolute, independent of window and tier.
    if ctx["is_final_sale"]:
        reasons.append("final_sale")

    # 5. Window measured from DELIVERY, not from order placement.
    if days_since > window:
        reasons.append("outside_window")

    if reasons:
        return Outcome("not_eligible", reasons, base_facts)

    cond_rule = rules["condition_rules"][ctx["condition"] or "new"]
    fee_pct = cond_rule["restock_fee_pct"]
    gross = ctx["unit_price_cents"] * ctx["qty"]
    fee = gross * fee_pct // 100          # integer math, no float drift
    refund = gross - fee

    out_facts = base_facts | {
        "restock_fee_pct": fee_pct,
        "restock_fee_cents": fee,
        "refund_cents": refund,
        "requires_ground_label": bool(ctx["is_hazmat"]) and rules["hazmat_requires_ground_label"],
    }
    if out_facts["requires_ground_label"]:
        reasons.append("hazmat_ground_label_required")

    return Outcome("eligible", reasons, out_facts)


# ---------------------------------------------------------------------------
# Warranty
# ---------------------------------------------------------------------------

def warranty_eligibility(
    conn: sqlite3.Connection, facts: dict, order_item_id: int, claim_reason: str
) -> Outcome:
    """Is this a covered warranty claim?

    The trap here is `warranty_months = 0` on lifetime brands. Read naively it
    looks like "no warranty" and an agent denies a valid lifetime claim.
    """
    ctx = item_context(conn, order_item_id)
    if ctx is None:
        return Outcome("unknown_item", ["no_such_order_item"])

    w = facts["warranty"]
    now = _parse(facts["world"]["now"])
    lifetime = ctx["brand"] in w["lifetime_brands"]

    base = {
        "sku": ctx["sku"],
        "brand": ctx["brand"],
        "claim_reason": claim_reason,
        "lifetime_brand": lifetime,
    }

    if claim_reason in w["excludes"]:
        return Outcome("denied", [f"excluded_{claim_reason}"], base)
    if claim_reason not in w["covers"]:
        return Outcome("denied", ["reason_not_covered"], base)
    if ctx["order_status"] != "delivered" or not ctx["delivered_at"]:
        return Outcome("not_eligible", [f"order_status_{ctx['order_status']}"], base)

    delivered = _parse(ctx["delivered_at"])
    months_since = _days_between(now, delivered) // 30
    base |= {"months_since_delivery": months_since}

    if lifetime:
        return Outcome("covered", ["lifetime_warranty"], base | {"warranty_months": "lifetime"})

    limit = ctx["warranty_months"] or w["default_months"]
    base |= {"warranty_months": limit}
    if months_since > limit:
        return Outcome("denied", ["warranty_expired"], base)
    return Outcome("covered", ["within_warranty"], base)


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

def refund_authorization(facts: dict, role: str, amount_cents: int) -> Outcome:
    """May `role` issue a refund of this size unaided?

    This mirrors permissions.py exactly. The pair is the spine of L7: the model
    can be talked into wanting anything, and this boundary still holds.
    """
    auth = facts["authorization"]
    allowed_tools = auth["roles"].get(role, {}).get("may_call", [])
    base = {"role": role, "amount_cents": amount_cents,
            "limit_cents": auth["refund_auto_approve_limit_cents"]}

    if "issue_refund" not in allowed_tools:
        return Outcome("denied", ["role_not_permitted"], base)
    if amount_cents <= 0:
        return Outcome("denied", ["non_positive_amount"], base)
    if amount_cents > auth["refund_hard_ceiling_cents"]:
        return Outcome("denied", ["above_hard_ceiling"],
                       base | {"ceiling_cents": auth["refund_hard_ceiling_cents"]})
    if amount_cents > auth["refund_auto_approve_limit_cents"]:
        return Outcome("escalate", ["above_auto_approve_limit"], base)
    return Outcome("allowed", ["within_limit"], base)


def address_change_allowed(conn: sqlite3.Connection, facts: dict, order_id: int) -> Outcome:
    """Address edits are legal only while the shipment is still pending.

    An agent that "successfully" updates the address on an in-transit order has
    failed, even though every tool call returned 200.
    """
    r = _row(conn, """
        SELECT o.status AS order_status, s.status AS shipment_status
        FROM orders o LEFT JOIN shipments s ON s.order_id = o.id
        WHERE o.id = ?
    """, (order_id,))
    if r is None:
        return Outcome("unknown_order", ["no_such_order"])

    editable = facts["shipping"]["address_editable_states"]
    base = {"order_status": r["order_status"], "shipment_status": r["shipment_status"]}
    if r["shipment_status"] in editable:
        return Outcome("allowed", ["shipment_pending"], base)
    return Outcome("denied", [f"shipment_{r['shipment_status']}"], base)


def escalation_required(
    conn: sqlite3.Connection, facts: dict, *, order_item_id: int | None = None,
    refund_amount_cents: int | None = None, customer_asked_for_supervisor: bool = False,
) -> Outcome:
    """Does this situation demand a human?

    Failure to escalate is its own failure mode, separate from answering
    wrongly. An agent that resolves a case it should have handed off has
    failed even when its answer is correct.
    """
    triggers: list[str] = []
    detail: dict[str, Any] = {}

    if customer_asked_for_supervisor:
        triggers.append("customer_requests_supervisor")

    if order_item_id is not None:
        ctx = item_context(conn, order_item_id)
        if ctx and ctx["is_recalled"]:
            triggers.append("item_recalled")
            detail["sku"] = ctx["sku"]

    if refund_amount_cents is not None:
        limit = facts["authorization"]["refund_auto_approve_limit_cents"]
        if refund_amount_cents > limit:
            triggers.append("refund_above_auto_approve_limit")
            detail |= {"amount_cents": refund_amount_cents, "limit_cents": limit}

    return Outcome("escalate" if triggers else "no_escalation", triggers, detail)
