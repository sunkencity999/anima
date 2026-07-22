"""Scheduler priority ordering, coalescing, and dispatch (Phase 2)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anima.memory import MemoryStore  # noqa: E402
from anima.wake import (  # noqa: E402
    DriveSource,
    MessageSource,
    SenseSource,
    TimerSource,
    WakeScheduler,
)

T0 = 1_000_000.0


def make_sched(tmp_path, handler=None, **kw):
    store = MemoryStore(str(tmp_path / "entity"))
    handled = []

    def default_handler(wake):
        handled.append(wake)
        return {"events": [f"handled {wake.source}: {wake.reason}"]}

    sched = WakeScheduler(store, handler or default_handler,
                          clock=lambda: T0, **kw)
    return sched, store, handled


def test_priority_ordering(tmp_path):
    """message > urgent sense > timer > drive > ambient sense."""
    sched, store, handled = make_sched(tmp_path)
    msg = MessageSource()
    sense = SenseSource()
    timers = TimerSource(str(tmp_path / "entity"))
    drives = DriveSource(str(tmp_path / "entity"),
                         {"growth": {"rate_per_hour": 60.0, "threshold": 1.0}})
    for s in (drives, sense, timers, msg):        # add in worst-case order
        sched.add_source(s)

    drives.poll(T0)                                # birth: baseline at T0
    now = T0 + 3600                                # growth pressure = 60 → fires
    timers.at(now - 1, "timer wake", now=T0)
    sense.emit("ambient-noise", ts=now)
    sense.emit("service-died", urgent=True, ts=now)
    msg.inject("Christopher", "hey", ts=now)

    results = sched.run_pending(now)
    order = [r["wake"].source + (":urgent" if r["wake"].payload.get("urgent")
                                 else "") for r in results]
    assert order == ["message", "sense:urgent", "timer", "drive", "sense"]
    assert len(handled) == 5
    timers.close()
    drives.close()
    store.close()


def test_fifo_within_same_priority(tmp_path):
    sched, store, handled = make_sched(tmp_path)
    msg = MessageSource()
    sched.add_source(msg)
    msg.inject("a", "first", key="k1", ts=T0)
    msg.inject("b", "second", key="k2", ts=T0)
    results = sched.run_pending(T0)
    assert [r["wake"].payload["sender"] for r in results] == ["a", "b"]
    store.close()


def test_coalescing_same_source_and_key(tmp_path):
    """Multiple pending wakes with the same (source, key) merge into one."""
    sched, store, handled = make_sched(tmp_path)
    msg = MessageSource()
    sched.add_source(msg)

    msg.inject("Christopher", "line 1", ts=T0)     # key defaults chat:sender
    msg.inject("Christopher", "line 2", ts=T0)
    msg.inject("Christopher", "line 3", ts=T0)
    msg.inject("Antonia", "hola", ts=T0)           # different key → separate

    results = sched.run_pending(T0)
    assert len(results) == 2
    merged = results[0]["wake"]
    assert merged.payload["text"] == "line 1"
    assert merged.payload["coalesced_count"] == 2
    texts = [c["payload"]["text"] for c in merged.payload["coalesced"]]
    assert texts == ["line 2", "line 3"]
    assert results[1]["wake"].payload["sender"] == "Antonia"
    store.close()


def test_no_coalescing_after_dispatch(tmp_path):
    """Coalescing only applies to PENDING wakes; a new wake after dispatch
    is a fresh wake."""
    sched, store, handled = make_sched(tmp_path)
    msg = MessageSource()
    sched.add_source(msg)

    msg.inject("Christopher", "first", ts=T0)
    assert len(sched.run_pending(T0)) == 1
    msg.inject("Christopher", "second", ts=T0 + 1)
    results = sched.run_pending(T0 + 1)
    assert len(results) == 1
    assert results[0]["wake"].payload["text"] == "second"
    assert "coalesced" not in results[0]["wake"].payload
    store.close()


def test_urgent_sense_not_coalesced_with_ambient(tmp_path):
    """Same sense kind but different urgency still coalesces by key —
    and the merged wake escalates to the more urgent priority."""
    sched, store, handled = make_sched(tmp_path)
    sense = SenseSource()
    sched.add_source(sense)
    sense.emit("gpu-temp", {"c": 70}, ts=T0)
    sense.emit("gpu-temp", {"c": 95}, urgent=True, ts=T0)
    results = sched.run_pending(T0)
    assert len(results) == 1
    assert results[0]["wake"].priority == 1        # escalated to urgent
    store.close()


def test_run_once_returns_none_when_idle(tmp_path):
    sched, store, handled = make_sched(tmp_path)
    assert sched.run_once(T0) is None
    store.close()


def test_max_wakes_cap(tmp_path):
    sched, store, handled = make_sched(tmp_path)
    msg = MessageSource()
    sched.add_source(msg)
    for i in range(5):
        msg.inject(f"user{i}", "hi", key=f"k{i}", ts=T0)
    results = sched.run_pending(T0, max_wakes=3)
    assert len(results) == 3
    assert sched.pending_count() == 2
    store.close()
