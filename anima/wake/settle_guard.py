"""Enforced settle phase (ARCHITECTURE.md §1 step 3).

"Settlement is enforced by the runtime, not left to the agent's
discipline." This module is that enforcement. A handler run through
run_settled / SettleGuard CANNOT skip settlement:

- handler returns a wake-report dict → settled as-is.
- handler returns None (or any non-dict) → a minimal report is
  synthesized ("wake completed without report") and settled.
- handler raises → the exception is captured into a failure report,
  settled, and then re-raised to the caller (the scheduler decides
  whether a failed wake is fatal; memory gets the episode either way).

The scheduler never exposes an unguarded dispatch path — the only way
a handler runs is inside this guard.
"""

from __future__ import annotations

import traceback
from typing import Any, Callable, Optional

from ..memory.settle import settle
from ..memory.store import MemoryStore
from .sources import Wake


def _synthesize_no_report(wake: Wake, now: float) -> dict:
    return {
        "wake_id": wake.wake_id,
        "ts": now,
        "events": [{
            "summary": f"wake completed without report ({wake.source}: {wake.reason})",
            "detail": "Handler returned no wake-report dict; settlement "
                      "synthesized by settle_guard.",
            "kind": "event",
            "tags": ["settle-guard", "no-report", wake.source],
        }],
    }


def _synthesize_failure(wake: Wake, exc: BaseException, now: float) -> dict:
    return {
        "wake_id": wake.wake_id,
        "ts": now,
        "events": [{
            "summary": f"wake FAILED ({wake.source}: {wake.reason}): "
                       f"{type(exc).__name__}: {exc}",
            "detail": traceback.format_exc(),
            "kind": "event",
            "tags": ["settle-guard", "failure", wake.source],
        }],
    }


def run_settled(
    store: MemoryStore,
    handler: Callable[[Wake], Optional[dict]],
    wake: Wake,
    *,
    now: float,
    reraise: bool = False,
) -> dict:
    """Run handler(wake); settlement happens no matter what.

    Returns {"receipt": <settle receipt>, "report": <report settled>,
             "ok": bool, "error": str | None}.
    If reraise=True a handler exception propagates AFTER settlement.
    """
    error: Optional[str] = None
    exc_caught: Optional[BaseException] = None
    try:
        report = handler(wake)
    except BaseException as exc:  # noqa: BLE001 — settle even on weird exits
        exc_caught = exc
        error = f"{type(exc).__name__}: {exc}"
        report = _synthesize_failure(wake, exc, now)
    else:
        if not isinstance(report, dict):
            report = _synthesize_no_report(wake, now)
        else:
            report = dict(report)
            report.setdefault("wake_id", wake.wake_id)
            report.setdefault("ts", now)

    receipt = settle(store, report)
    result = {"receipt": receipt, "report": report,
              "ok": exc_caught is None, "error": error}
    if exc_caught is not None and reraise:
        raise exc_caught
    return result


class SettleGuard:
    """Context-manager form: settlement guaranteed at __exit__.

        with SettleGuard(store, wake, now=now) as guard:
            ...do work...
            guard.report = {"events": [...]}

    If the body never sets guard.report, or raises, a synthesized report
    is settled anyway. guard.receipt holds the settle receipt afterward.
    """

    def __init__(self, store: MemoryStore, wake: Wake, *, now: float):
        self.store = store
        self.wake = wake
        self.now = now
        self.report: Optional[dict] = None
        self.receipt: Optional[dict] = None

    def __enter__(self) -> "SettleGuard":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None:
            report = _synthesize_failure(self.wake, exc, self.now)
        elif isinstance(self.report, dict):
            report = dict(self.report)
            report.setdefault("wake_id", self.wake.wake_id)
            report.setdefault("ts", self.now)
        else:
            report = _synthesize_no_report(self.wake, self.now)
        self.receipt = settle(self.store, report)
        return False  # never swallow exceptions; memory already has them
