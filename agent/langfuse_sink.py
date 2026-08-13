"""Mirror the trace tree into Langfuse, live, while the agent runs.

This is the L2 backend. `agent/tracing.py` stays the source of truth: it is
what the corpus is built from and what every browser lab reads. This module
only mirrors, and it is off unless you switch it on.

    make trace-up            bring the stack up
    make trace-demo          run the agent, watch spans arrive
    open http://localhost:3000

WHY MIRROR LIVE RATHER THAN UPLOAD AFTERWARDS

The obvious design is to finish a run, take `trace.to_dict()`, and post it.
That design quietly destroys the timeline. Langfuse v3 and later are built on
OpenTelemetry, where a span's start time is the moment you open it. Replaying a
finished trace opens every span at once, so a run that really took four seconds
arrives as a flat stack of near-zero-duration spans, all starting together.
Latency becomes unreadable, which is most of why you wanted the tool.

Mirroring as spans open and close means the timings are the real ones, and it
matches what the tracing module already says about clocks: the world clock is
frozen so expected outcomes cannot drift, but trace timestamps are wall clock
because they record what actually happened.

WHY A FAILURE HERE MUST NEVER FAIL A RUN

Telemetry is not the workload. If ClickHouse is wedged or the container is
still starting, the correct outcome is a run with no telemetry, never a crashed
agent. Every callback below is wrapped for that reason, and the first failure
prints one line and then goes quiet rather than printing per span.

HOW THE THREE STATUSES MAP

    ok      -> DEFAULT
    error   -> ERROR
    denied  -> WARNING

Denials get their own level on purpose. A blocked attack is not an error: the
system did exactly what it was built to do. If denials were logged as errors
they would be filtered out with the noise, and L7 needs to be able to show the
boundary holding. If they were logged as DEFAULT they would be invisible. A
separate level is the only mapping that keeps them findable.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

# The local stack in infra/docker-compose.yml pre-provisions this project with
# these exact keys, so the defaults are enough on a laptop and nothing has to be
# copied out of the UI. Override for Langfuse Cloud. See infra/langfuse.env.
DEFAULT_HOST = "http://localhost:3000"
DEFAULT_PUBLIC_KEY = "pk-lf-wayfarer-local-dev-public"
DEFAULT_SECRET_KEY = "sk-lf-wayfarer-local-dev-secret"

_TRUTHY = {"1", "true", "yes", "on"}

_LEVEL = {"ok": "DEFAULT", "error": "ERROR", "denied": "WARNING"}


def enabled() -> bool:
    """One switch, and only one.

    Deliberately not "on when keys happen to be present". Implicit activation
    means someone exports a key for an unrelated reason and the agent silently
    starts shipping customer messages to a server. Telemetry that turns itself
    on is a privacy incident waiting for a reason.
    """
    return os.environ.get("LANGFUSE_TRACING", "").strip().lower() in _TRUTHY


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------
# L2 lists "do not log the whole prompt on every span" as a trap, on the
# grounds that traces start carrying customer data into systems never scoped to
# hold it. A mask is how that trap is answered in code rather than in a comment.
#
# This is deliberately crude, and its crudeness is the lesson. It catches the
# two shapes that show up in Wayfarer's support messages. It is not a PII
# detector, and treating any regex as one is how data ends up somewhere it
# should not be. Real redaction is a scoping decision about what you send at
# all, made before this point.

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_LONG_DIGITS = re.compile(r"\b\d{12,19}\b")  # card-shaped


def _scrub(text: str) -> str:
    text = _EMAIL.sub("[email]", text)
    return _LONG_DIGITS.sub("[redacted-number]", text)


def mask(data: Any = None, **_: Any) -> Any:
    """Recursively redact. Signature is loose because the SDK calls it by
    keyword and the exact name has moved between versions."""
    try:
        if isinstance(data, str):
            return _scrub(data)
        if isinstance(data, dict):
            return {k: mask(data=v) for k, v in data.items()}
        if isinstance(data, (list, tuple)):
            return [mask(data=v) for v in data]
        return data
    except Exception:  # noqa: BLE001
        return "[mask failed]"


# ---------------------------------------------------------------------------
# The sink
# ---------------------------------------------------------------------------

class LangfuseSink:
    """Mirrors span open/close into Langfuse observations.

    Attached to a Trace at construction. Holds one Langfuse observation per
    open span, keyed by our span id, so nesting is reproduced exactly.
    """

    def __init__(self, client: Any, propagate: Any) -> None:
        self._client = client
        self._propagate = propagate
        self._obs: dict[str, Any] = {}
        # One open propagate_attributes context per trace, keyed by root span.
        # It is entered before the root observation exists and exited when the
        # trace finishes. OpenTelemetry context is per-thread, so concurrent
        # corpus workers each get their own and do not overwrite each other.
        self._ctx: dict[str, Any] = {}
        self._warned = False
        # Counted rather than hardcoded by callers. A fixed number in a print
        # statement goes stale the moment someone adds a case, and then the
        # tool that exists to tell you the truth is the one lying to you.
        self.traces = 0

    # -- construction ------------------------------------------------------

    @classmethod
    def create(cls, *, quiet: bool = False) -> "LangfuseSink | None":
        """Return a sink, or None with a reason on stderr. Never raises."""
        if not enabled():
            return None
        try:
            # propagate_attributes is a module-level function, not a client or
            # span method. Trace-level fields (name, session, tags) can only be
            # set through it: LangfuseSpan in v4 has no update_trace at all, and
            # calling one leaves every trace unnamed in the UI.
            from langfuse import Langfuse, propagate_attributes  # noqa: PLC0415
        except ImportError:
            if not quiet:
                print("LANGFUSE_TRACING is set but the langfuse package is not "
                      "installed.\n  pip install -r requirements-tracing.txt",
                      file=sys.stderr)
            return None

        try:
            client = Langfuse(
                public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", DEFAULT_PUBLIC_KEY),
                secret_key=os.environ.get("LANGFUSE_SECRET_KEY", DEFAULT_SECRET_KEY),
                host=os.environ.get("LANGFUSE_HOST", DEFAULT_HOST),
                environment=os.environ.get("LANGFUSE_ENVIRONMENT", "course"),
                mask=mask,
                # Small batches: a demo that shows nothing for thirty seconds
                # reads as broken, and the volume here is tiny.
                flush_at=8,
                flush_interval=1.0,
            )
        except Exception as e:  # noqa: BLE001
            if not quiet:
                print(f"langfuse: client init failed, continuing without "
                      f"telemetry ({type(e).__name__}: {e})", file=sys.stderr)
            return None

        # auth_check is a real round trip. Doing it once here turns "the UI is
        # empty and nobody knows why" into one clear line before the run starts.
        try:
            if hasattr(client, "auth_check") and not client.auth_check():
                if not quiet:
                    print(f"langfuse: credentials rejected by "
                          f"{os.environ.get('LANGFUSE_HOST', DEFAULT_HOST)}. "
                          f"Is the stack up? Try: make trace-up", file=sys.stderr)
                return None
        except Exception as e:  # noqa: BLE001
            if not quiet:
                print(f"langfuse: cannot reach "
                      f"{os.environ.get('LANGFUSE_HOST', DEFAULT_HOST)} "
                      f"({type(e).__name__}). Try: make trace-up", file=sys.stderr)
            return None

        return cls(client, propagate_attributes)

    # -- internals ---------------------------------------------------------

    def _warn_once(self, exc: Exception) -> None:
        if not self._warned:
            self._warned = True
            print(f"langfuse: telemetry failed mid-run, continuing without it "
                  f"({type(exc).__name__}: {exc})", file=sys.stderr)

    @staticmethod
    def _as_type(kind: str) -> str:
        # model_call carries tokens and cost, which is exactly what a Langfuse
        # generation is for. Everything else is a plain span.
        return "generation" if kind == "model_call" else "span"

    # -- callbacks ---------------------------------------------------------

    def on_span_open(self, trace: Any, span: Any) -> None:
        try:
            kwargs: dict[str, Any] = {
                "name": span.name,
                "as_type": self._as_type(span.kind),
                "metadata": dict(span.attributes),
            }
            if span.kind == "model_call":
                kwargs["model"] = trace.model

            parent = self._obs.get(span.parent_id) if span.parent_id else None
            if parent is not None:
                obs = parent.start_observation(**kwargs)
            else:
                # Root span. The propagation context has to be entered BEFORE
                # the observation is created, or the root itself is left without
                # the trace name and the list view shows a row of blanks.
                ctx = self._propagate(
                    trace_name=f"{trace.scenario_id} ({trace.role})",
                    session_id=trace.scenario_id,
                    tags=[trace.role, trace.model],
                    metadata={k: v for k, v in trace.metadata.items() if v is not None},
                )
                ctx.__enter__()
                self._ctx[span.span_id] = ctx
                obs = self._client.start_observation(**kwargs)

            self._obs[span.span_id] = obs
        except Exception as e:  # noqa: BLE001
            self._warn_once(e)

    def set_input(self, trace: Any, text: str) -> None:
        """Show the customer's message on the trace.

        Passed in explicitly rather than read off trace.metadata, because
        metadata is serialised into the committed corpus and adding a field
        there would change bytes that CI checks for staleness. Telemetry should
        not be able to alter the artifact it is observing.
        """
        obs = self._obs.get(trace.root_id)
        if obs is None:
            return
        try:
            obs.update(input=text)
        except Exception as e:  # noqa: BLE001
            self._warn_once(e)

    def on_span_close(self, trace: Any, span: Any) -> None:
        # pop, so a span closed twice (finish() sweeps anything still open)
        # cannot end an observation twice
        obs = self._obs.pop(span.span_id, None)
        if obs is None:
            return
        try:
            update: dict[str, Any] = {
                "level": _LEVEL.get(span.status, "DEFAULT"),
                "metadata": dict(span.attributes),
            }
            if span.status == "denied":
                # Two shapes carry the reason. A refused tool_call records
                # denied_reason; a guard span from record_denial records reason.
                # Reading only one of them leaves half the denials in the UI
                # saying "unspecified", which is the field you actually opened
                # the trace to read.
                why = (span.attributes.get("denied_reason")
                       or span.attributes.get("reason")
                       or "unspecified")
                update["status_message"] = f"permission denied: {why}"
            elif span.status == "error":
                exc = next((e for e in span.events if e.name == "exception"), None)
                if exc is not None:
                    update["status_message"] = (
                        f"{exc.attributes.get('type')}: {exc.attributes.get('message')}"
                    )
            if span.kind == "model_call":
                usage = {"input": span.input_tokens, "output": span.output_tokens}
                if span.cached_tokens:
                    usage["cache_read_input_tokens"] = span.cached_tokens
                update["usage_details"] = usage
                # Our own number, from evals/pricing.json. Sending it means the
                # UI agrees with the L9 lab instead of quietly recomputing from
                # a price table that was right about a different month.
                update["cost_details"] = {"total": round(span.cost_usd, 6)}
            obs.update(**update)
            obs.end()
        except Exception as e:  # noqa: BLE001
            self._warn_once(e)

    def on_finish(self, trace: Any) -> None:
        try:
            root = self._obs.pop(trace.root_id, None)
            if root is not None:
                # A run that exhausted its step budget has no final_response and
                # no error. Saying so beats an empty cell that reads as a bug in
                # the tracing rather than the thing L2 wants you to notice.
                root.update(output=trace.final_response or trace.error
                            or "(no final answer: step budget exhausted)")
                root.end()
            ctx = self._ctx.pop(trace.root_id, None)
            if ctx is not None:
                ctx.__exit__(None, None, None)
            self._obs.clear()
            self.traces += 1
        except Exception as e:  # noqa: BLE001
            self._warn_once(e)

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception as e:  # noqa: BLE001
            self._warn_once(e)


def get_sink(*, quiet: bool = False) -> "LangfuseSink | None":
    """Single entry point. Returns None whenever tracing is off, the package is
    missing, or the server is unreachable, so callers never branch on config."""
    return LangfuseSink.create(quiet=quiet)
