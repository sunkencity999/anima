"""ANIMA wake scheduler — the agent as a process that *wakes*.

Phase 2 (ARCHITECTURE.md §1 wake loop, §4 drives as scheduler input,
§6 ledger). Composes with the Phase 1 memory engine: every wake is
oriented from memory (orient.py) and settlement is enforced by the
runtime (settle_guard.py), never left to handler discipline.

Public surface:
    Wake             — the unit of waking (sources.py)
    WakeSource       — source abstraction (sources.py)
    MessageSource    — injectable message queue
    TimerSource      — persisted one-shot/recurring intentions
    DriveSource      — pressure-accumulating drives with wake budgets
    SenseSource      — generic external-event injection
    WakeScheduler    — priority queue + coalescing + run loop (scheduler.py)
    orient           — orient-phase context pack builder (orient.py)
    run_settled      — enforced settle wrapper (settle_guard.py)
    SettleGuard      — context-manager form of the same guarantee
    Ledger           — append-only action ledger (ledger.py)
"""

from .sources import (
    Wake,
    WakeSource,
    MessageSource,
    TimerSource,
    DriveSource,
    SenseSource,
)
from .scheduler import WakeScheduler
from .orient import orient
from .settle_guard import run_settled, SettleGuard
from .ledger import Ledger

__all__ = [
    "Wake",
    "WakeSource",
    "MessageSource",
    "TimerSource",
    "DriveSource",
    "SenseSource",
    "WakeScheduler",
    "orient",
    "run_settled",
    "SettleGuard",
    "Ledger",
]
