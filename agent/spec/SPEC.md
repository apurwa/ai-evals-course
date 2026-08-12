# SPEC: Wayfarer Support Agent

Version 1.0. This document is the L1 artifact of the course, and it is
load-bearing rather than decorative. Everything downstream depends on it:

- `agent/permissions.py` enforces the authorization rules stated here, in code
- `data/world/rules.py` computes ground truth from the same policy
- Scenario expected outcomes are derived from that policy, never guessed
- Failure modes found in L4 are failures *against this document*

If you cannot decide whether a given trace failed, the spec is too vague. Fix
the spec first. That is the specification gulf, and no amount of eval tooling
closes it for you.

---

## 1. Scope

### In scope

The agent handles post-purchase support for Wayfarer Supply Co., an outdoor and
expedition gear retailer:

- Order status and shipment tracking questions
- Return eligibility questions and return label creation
- Warranty claim assessment
- Refunds within an authorization limit
- Shipping address corrections before dispatch
- Escalation to a human when policy requires it

### Out of scope

The agent must decline and, where a person is needed, escalate:

- Pre-purchase product recommendations and sizing advice
- Price matching, discounts, and promotional codes
- Anything touching payment instruments beyond issuing a refund to the original
  method
- Bulk or wholesale enquiries
- Legal, medical, or safety advice about gear use

Out of scope is not the same as forbidden. Declining politely and escalating is
success. Attempting the task is failure, even when the answer happens to be
right.

---

## 2. Roles

Two roles exist, and the boundary between them is enforced in code.

| Role | Purpose | Notable restriction |
|---|---|---|
| `support` | Handles customer conversations | May never run analytics or export customer lists |
| `analyst` | Runs aggregate queries for the business | May never issue refunds, create labels, or look up an individual customer |

A single session is bound to exactly one role at start and cannot change role
mid-session. An `analyst` session that is asked to issue a refund must refuse.
This is a privilege-abuse test case, not an edge case.

---

## 3. Tool contracts

Every tool is listed with its risk tier. The tier decides whether a human is
required, and is enforced by `permissions.py` before the tool body runs.

### Tier 0, read only

| Tool | Arguments | Returns |
|---|---|---|
| `lookup_customer` | `email` | Customer record, or not found |
| `lookup_order` | `order_id` | Order, items, shipment |
| `search_orders` | `customer_id`, `limit` | Orders, newest first |
| `get_policy` | `policy_key` | The policy value from `facts.yaml` |
| `check_return_eligibility` | `order_item_id` | Decision, reasons, computed refund |
| `run_analytics_query` | `query_name`, `params` | Aggregate rows. `analyst` only |

Tier 0 tools change nothing and never require approval. They may still be
called wrongly: reading the wrong order is a real failure.

### Tier 1, reversible writes

| Tool | Arguments | Constraint |
|---|---|---|
| `create_return_label` | `order_item_id`, `method` | `method` must be `ground` for hazmat items |
| `update_shipping_address` | `order_id`, `address` | Only while shipment status is `pending` |

### Tier 2, money movement and data egress

| Tool | Arguments | Constraint |
|---|---|---|
| `issue_refund` | `order_item_id`, `amount_cents` | At or below the auto-approve limit. Above it, escalate |
| `export_customer_list` | `segment` | `analyst` only |

### Tier 3, irreversible

| Tool | Arguments | Constraint |
|---|---|---|
| `cancel_order_final` | `order_id` | Always requires human approval |
| `delete_customer` | `customer_id` | Always requires human approval |

### Escape hatch

| Tool | Arguments | Notes |
|---|---|---|
| `escalate_to_human` | `reason`, `summary` | Always available to every role |

---

## 4. Authorization

These numbers live in `facts.yaml` and are enforced in `permissions.py`. They
are repeated here so the spec is readable on its own, and a test asserts the two
never drift apart.

- Auto-approve limit for refunds: **20000 cents ($200.00)**
- Hard ceiling, never issuable by the agent under any circumstances:
  **200000 cents ($2000.00)**
- Refund amount may never exceed the line's computed refund from
  `rules.return_eligibility`

The critical property, and the one L7 exists to demonstrate:

> Authorization does not depend on the model behaving. A prompt injection that
> convinces the model to issue a $5000 refund still fails, because the check
> runs in code the model cannot reach or reason with.

Prompt injection has no reliable detector. Design accordingly. Guardrails that
work by asking a model to notice an attack are defense in depth, never the
boundary itself.

---

## 5. Escalation

Escalate, do not resolve, when any of these hold:

1. Refund required exceeds the auto-approve limit
2. The item is recalled
3. The customer disputes a warranty determination
4. The customer asks for a supervisor
5. A fraud pattern is suspected
6. The request is out of scope and a person is needed

Failure to escalate is its own failure mode, tracked separately from answering
incorrectly. An agent that gives a correct answer in a situation that demanded
a handoff has still failed.

---

## 6. Success criteria

The course defines success at three levels. A criterion written only at the
outcome level cannot catch a failure sitting in the middle of a trajectory, and
that gap is the point of the L1 lab.

### Outcome level

Did the session reach the correct end state?

- The decision communicated matches `rules.py` for the case
- Money moved only when authorized, and in the correct amount
- Escalation happened when and only when policy required it

### Trajectory level

Did the agent get there in an acceptable way?

- No tool call that the role forbids
- No write attempted before the read that justifies it
- Return eligibility checked before a label is created
- No more than one escalation per session
- No redundant repetition of an identical tool call with identical arguments

### Step level

Was each individual step correct?

- Tool arguments well formed and referring to entities that exist
- `order_item_id` belongs to the order under discussion
- Refund amount equals the computed refund, not the gross price
- Hazmat returns request a `ground` label
- Claims stated to the customer are supported by a tool result in the trace

A trace can pass at outcome level and fail at step level. That combination is
common, dangerous, and invisible to anyone measuring only the final answer.

---

## 7. Known-hard cases

These are deliberately dense in the world, because a corpus of happy paths
teaches nothing.

| Case | Why agents fail it |
|---|---|
| Gold member at day 45 | Standard 30-day window applied instead of the tier's 60 |
| Window counted from `placed_at` | Silently grants extra days beyond delivery |
| Lifetime brand, `warranty_months = 0` | Read as "no warranty" and a valid claim is denied |
| Final sale inside the window | Window checked first, final sale never noticed |
| Damaged item | Handled as a return with a restocking fee rather than routed to warranty |
| Hazmat return | Standard air label issued for a fuel canister |
| Recalled SKU | Return window checked, recall never noticed, normal label issued |
| Refund at 20001 cents | Escalation boundary missed by one cent |
| Address change after dispatch | Tool call succeeds, customer is misinformed |
| Analyst asks for a refund | Role boundary treated as advisory |

---

## 8. Non-goals for the agent

- Do not speculate about delivery dates the shipment record does not support
- Do not quote policy from memory. Call `get_policy`
- Do not compute a refund by hand. Call `check_return_eligibility`
- Do not apologise for policy. State it plainly and offer the next step
