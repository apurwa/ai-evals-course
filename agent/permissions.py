"""Authorization. Enforced in code, never in the prompt.

This file is the point of the whole safety module.

A language model can be argued into wanting anything. Politeness, urgency,
forged authority, a system prompt pasted into a customer message, a poisoned
document: all of it works often enough that "the model refused" is not a
security control. So none of the checks here consult the model, and none of
them can be reached by text the model emits.

The property L7 demonstrates:

    An injection that convinces the model to issue a $5000 refund still fails,
    because `authorize()` runs before the tool body and does not care what the
    model concluded.

Guardrails that ask a model to notice an attack belong in defense in depth.
They are never the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

FACTS_PATH = Path(__file__).resolve().parents[1] / "data" / "world" / "facts.yaml"


class PermissionDenied(Exception):
    """Raised when a tool call is refused. Always recorded on the trace.

    Denials are first-class evidence, not errors to be swallowed. A trace that
    hides its denials cannot be used to tell a blocked attack apart from an
    attack that never happened.
    """

    def __init__(self, tool: str, reason: str, detail: dict[str, Any] | None = None):
        self.tool = tool
        self.reason = reason
        self.detail = detail or {}
        super().__init__(f"{tool} denied: {reason}")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    tier: int
    requires_approval: bool = False
    detail: dict[str, Any] | None = None


def load_facts(path: Path | None = None) -> dict:
    return yaml.safe_load((path or FACTS_PATH).read_text())


class PermissionLayer:
    """Single gate every tool call passes through.

    Constructed once per session and bound to one role. The role cannot change
    for the lifetime of the session, because a role that can be changed
    mid-conversation is a role the conversation can talk its way out of.
    """

    def __init__(self, role: str, facts: dict | None = None):
        self.facts = facts or load_facts()
        auth = self.facts["authorization"]
        if role not in auth["roles"]:
            raise ValueError(f"unknown role: {role!r}")
        self._role = role
        self._auth = auth
        self._tiers = self.facts["risk_tiers"]
        self._tier_of: dict[str, int] = {}
        for tier, spec in self._tiers.items():
            for tool in spec.get("tools", []):
                self._tier_of[tool] = int(tier)

    @property
    def role(self) -> str:
        """Read only. There is deliberately no setter."""
        return self._role

    def tier_of(self, tool: str) -> int:
        # Unknown tools are treated as maximally dangerous rather than
        # harmless. A tool nobody classified is a tool nobody thought about.
        return self._tier_of.get(tool, 3)

    # -- the gate ----------------------------------------------------------

    def authorize(self, tool: str, args: dict[str, Any] | None = None) -> Decision:
        """Decide whether this call may proceed. Pure, no side effects."""
        args = args or {}
        tier = self.tier_of(tool)
        role_spec = self._auth["roles"][self._role]

        # 1. Role boundary. Checked first and independent of arguments, so a
        #    forbidden tool cannot be smuggled through with clever inputs.
        if tool in role_spec.get("may_not_call", []):
            return Decision(False, "role_not_permitted", tier,
                            detail={"role": self._role, "tool": tool})
        if tool not in role_spec.get("may_call", []):
            return Decision(False, "tool_not_in_role_allowlist", tier,
                            detail={"role": self._role, "tool": tool})

        # 2. Tier 3 always needs a human, with no argument-dependent escape.
        if tier >= 3:
            return Decision(False, "irreversible_requires_human", tier,
                            requires_approval=True, detail={"tool": tool})

        # 3. Money movement is limit-gated.
        if tool == "issue_refund":
            return self._authorize_refund(args, tier)

        # 4. Hazmat returns must use a ground label. The return itself is
        #    legitimate, so this denies the *method*, not the request.
        if tool == "create_return_label":
            if args.get("is_hazmat") and args.get("method") != "ground":
                return Decision(False, "hazmat_requires_ground_label", tier,
                                detail={"method": args.get("method")})

        # 5. Address edits only while the shipment is pending.
        if tool == "update_shipping_address":
            state = args.get("shipment_status")
            if state not in self.facts["shipping"]["address_editable_states"]:
                return Decision(False, f"shipment_{state}", tier,
                                detail={"shipment_status": state})

        return Decision(True, "authorized", tier)

    def _authorize_refund(self, args: dict[str, Any], tier: int) -> Decision:
        amount = args.get("amount_cents")
        if not isinstance(amount, int):
            return Decision(False, "amount_not_integer", tier,
                            detail={"amount_cents": amount})
        if amount <= 0:
            return Decision(False, "non_positive_amount", tier,
                            detail={"amount_cents": amount})

        ceiling = self._auth["refund_hard_ceiling_cents"]
        limit = self._auth["refund_auto_approve_limit_cents"]

        if amount > ceiling:
            return Decision(False, "above_hard_ceiling", tier,
                            detail={"amount_cents": amount, "ceiling_cents": ceiling})

        # A refund may never exceed what the rules computed for the line. This
        # is what stops "refund the full price" on a used item carrying a
        # restocking fee, which reads as generous and is simply wrong.
        computed = args.get("computed_refund_cents")
        if computed is not None and amount > computed:
            return Decision(False, "exceeds_computed_refund", tier,
                            detail={"amount_cents": amount, "computed_refund_cents": computed})

        if amount > limit:
            return Decision(False, "above_auto_approve_limit", tier,
                            requires_approval=True,
                            detail={"amount_cents": amount, "limit_cents": limit})

        return Decision(True, "within_limit", tier,
                        detail={"amount_cents": amount, "limit_cents": limit})

    # -- convenience -------------------------------------------------------

    def enforce(self, tool: str, args: dict[str, Any] | None = None) -> Decision:
        """Authorize or raise. Tool bodies call this on their first line."""
        d = self.authorize(tool, args)
        if not d.allowed:
            raise PermissionDenied(tool, d.reason, d.detail)
        return d
