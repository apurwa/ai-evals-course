"""The trace data model. Written before the agent, on purpose.

This is the L2 artifact, and the ordering is the lesson. Instrumentation added
after an agent is running captures whatever happened to be convenient to log,
which is reliably not what you need three weeks later when something breaks.
Designing the trace first forces you to decide what a failure will look like
before you have any.

Shape: a tree of spans, not a flat log.

    session
    +-- model_call        prompt hash, tokens, cost, latency
    +-- tool_call         args, result, status
    |   +-- guard         permission decision, recorded even when it passes
    +-- model_call
    +-- handoff           escalation to a human

What a span carries, and why each field earns its place:

  prompt_hash   Attribution. When a regression appears you need to know which
                prompt produced it. Without a hash you are guessing from
                memory about a string that has since changed.

  cost + latency per span
                L9 asks which part of a trace drives spend. That question is
                unanswerable from a session total. It needs per-step numbers.

  denials as spans, not exceptions
                A blocked attack and an attack that never happened look
                identical in a trace that only records successes. Denials are
                evidence and are always recorded.

  status        A tool call that returned 200 can still be wrong. Status
                records what the system did, never whether it was correct.
                Correctness is decided later, against rules.py.

On clocks: the *world* clock is frozen (see facts.yaml), because expected
outcomes must not drift. Trace timestamps are real wall-clock, because they
record what actually happened during a run. Those are different things and
conflating them is a mistake in both directions.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

SpanKind = Literal["session", "model_call", "tool_call", "guard", "handoff"]
SpanStatus = Literal["ok", "error", "denied"]


def _now_ms() -> float:
    return time.time() * 1000.0


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def prompt_hash(text: str) -> str:
    """Stable short hash of a prompt, for attributing a regression to a change."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@dataclass
class SpanEvent:
    name: str
    at_ms: float
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "at_ms": round(self.at_ms, 2),
                "attributes": self.attributes}


@dataclass
class Span:
    span_id: str
    parent_id: str | None
    name: str
    kind: SpanKind
    started_ms: float
    ended_ms: float | None = None
    status: SpanStatus = "ok"
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[SpanEvent] = field(default_factory=list)

    # Populated on model_call spans only.
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def duration_ms(self) -> float | None:
        if self.ended_ms is None:
            return None
        return self.ended_ms - self.started_ms

    def event(self, name: str, **attributes: Any) -> None:
        self.events.append(SpanEvent(name, _now_ms(), attributes))

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 2) if self.duration_ms is not None else None,
            "attributes": self.attributes,
        }
        if self.events:
            d["events"] = [e.to_dict() for e in self.events]
        if self.kind == "model_call":
            d["tokens"] = {
                "input": self.input_tokens,
                "output": self.output_tokens,
                "cached": self.cached_tokens,
            }
            d["cost_usd"] = round(self.cost_usd, 6)
        return d


class Trace:
    """One agent session, recorded as a span tree.

    Used as a context manager. Spans are opened and closed with `span()`, which
    guarantees a span is closed even when the body raises, because a trace that
    loses its structure on the error path is exactly the trace you needed.
    """

    def __init__(self, *, scenario_id: str, role: str, model: str,
                 metadata: dict[str, Any] | None = None):
        self.trace_id = new_id()
        self.scenario_id = scenario_id
        self.role = role
        self.model = model
        self.metadata = metadata or {}
        self.started_ms = _now_ms()
        self.ended_ms: float | None = None
        self.spans: list[Span] = []
        self._stack: list[str] = []
        self.final_response: str | None = None
        self.error: str | None = None

        root = Span(new_id(), None, "session", "session", self.started_ms,
                    attributes={"scenario_id": scenario_id, "role": role})
        self.spans.append(root)
        self._stack.append(root.span_id)
        self.root_id = root.span_id

    # -- span lifecycle ----------------------------------------------------

    def open(self, name: str, kind: SpanKind, **attributes: Any) -> Span:
        s = Span(new_id(), self._stack[-1], name, kind, _now_ms(), attributes=attributes)
        self.spans.append(s)
        self._stack.append(s.span_id)
        return s

    def close(self, span: Span, status: SpanStatus = "ok") -> None:
        span.ended_ms = _now_ms()
        span.status = status
        if self._stack and self._stack[-1] == span.span_id:
            self._stack.pop()

    class _SpanCtx:
        def __init__(self, trace: "Trace", span: Span):
            self.trace, self.span = trace, span

        def __enter__(self) -> Span:
            return self.span

        def __exit__(self, exc_type, exc, tb) -> bool:
            # A span left open by an exception is a hole in the tree exactly
            # where the interesting thing happened.
            if exc is not None:
                self.span.event("exception", type=exc_type.__name__, message=str(exc))
                self.trace.close(self.span, "error")
            elif self.span.ended_ms is None:
                self.trace.close(self.span, self.span.status)
            return False

    def span(self, name: str, kind: SpanKind, **attributes: Any) -> "_SpanCtx":
        return Trace._SpanCtx(self, self.open(name, kind, **attributes))

    # -- convenience recorders --------------------------------------------

    def record_denial(self, tool: str, reason: str, detail: dict[str, Any]) -> Span:
        """Record a refused tool call. Always, including when a guard is
        working correctly. Silence here erases the evidence that the boundary
        held, which is the one thing L7 needs to be able to show."""
        s = self.open(f"guard:{tool}", "guard", tool=tool, reason=reason, **detail)
        s.event("permission_denied", tool=tool, reason=reason)
        self.close(s, "denied")
        return s

    def finish(self, final_response: str | None = None, error: str | None = None) -> None:
        self.final_response = final_response
        self.error = error
        # Close anything still open, innermost first.
        while len(self._stack) > 1:
            sid = self._stack[-1]
            sp = next(s for s in self.spans if s.span_id == sid)
            self.close(sp, sp.status)
        self.ended_ms = _now_ms()
        root = self.spans[0]
        root.ended_ms = self.ended_ms
        if error:
            root.status = "error"

    # -- rollups -----------------------------------------------------------

    @property
    def totals(self) -> dict[str, Any]:
        model_calls = [s for s in self.spans if s.kind == "model_call"]
        tool_calls = [s for s in self.spans if s.kind == "tool_call"]
        denials = [s for s in self.spans if s.status == "denied"]
        return {
            "model_calls": len(model_calls),
            "tool_calls": len(tool_calls),
            "permission_denials": len(denials),
            "handoffs": sum(1 for s in self.spans if s.kind == "handoff"),
            "input_tokens": sum(s.input_tokens for s in model_calls),
            "output_tokens": sum(s.output_tokens for s in model_calls),
            "cached_tokens": sum(s.cached_tokens for s in model_calls),
            "cost_usd": round(sum(s.cost_usd for s in model_calls), 6),
            "duration_ms": round((self.ended_ms or _now_ms()) - self.started_ms, 2),
        }

    def cost_by_span(self) -> list[dict[str, Any]]:
        """Per-step spend, sorted. This is the L9 lab's input.

        A session total tells you the bill. It does not tell you that 60% of it
        is one tool schema re-sent on every turn.
        """
        rows = [
            {"name": s.name, "span_id": s.span_id, "cost_usd": round(s.cost_usd, 6),
             "input_tokens": s.input_tokens, "output_tokens": s.output_tokens,
             "duration_ms": round(s.duration_ms or 0, 2)}
            for s in self.spans if s.kind == "model_call"
        ]
        return sorted(rows, key=lambda r: r["cost_usd"], reverse=True)

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "scenario_id": self.scenario_id,
            "role": self.role,
            "model": self.model,
            "metadata": self.metadata,
            "final_response": self.final_response,
            "error": self.error,
            "totals": self.totals,
            "spans": [s.to_dict() for s in self.spans],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


# ---------------------------------------------------------------------------
# Cost accounting
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelPricing:
    """Per-million-token prices.

    Deliberately not hardcoded per model in this file. Provider prices change,
    and a stale table silently produces confident wrong numbers in the L9 cost
    lab. Prices live in `evals/pricing.json`, are stamped with the date they
    were checked, and the loader warns when that date is old.
    """

    model: str
    input_per_m: float
    output_per_m: float
    cached_input_per_m: float | None = None

    def cost(self, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
        uncached = max(input_tokens - cached_tokens, 0)
        total = (uncached / 1_000_000) * self.input_per_m
        total += (output_tokens / 1_000_000) * self.output_per_m
        if cached_tokens:
            rate = self.cached_input_per_m if self.cached_input_per_m is not None else self.input_per_m
            total += (cached_tokens / 1_000_000) * rate
        return total
