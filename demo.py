#!/usr/bin/env python3
"""ANIMA Phase 2 demo — a deterministic wake-scheduler simulation.

Seeds an entity with a little memory, defines two drives (curiosity,
stewardship), injects a message and a sense event, then advances a fake
clock through a few ticks while a toy echo handler services the wakes.
No sleeps, no network, no wall clock. Run:

    python3 demo.py
"""

from __future__ import annotations

import shutil
import tempfile

from anima.memory import MemoryStore
from anima.wake import (
    DriveSource,
    Ledger,
    MessageSource,
    SenseSource,
    TimerSource,
    WakeScheduler,
    orient,
)

T0 = 1_800_000_000.0  # arbitrary fixed epoch
HOUR = 3600.0


class FakeClock:
    def __init__(self, start: float):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


def main() -> None:
    root = tempfile.mkdtemp(prefix="anima-demo-")
    clock = FakeClock(T0)

    store = MemoryStore(root)
    ledger = Ledger(root)
    messages = MessageSource()
    senses = SenseSource()
    timers = TimerSource(root)
    drives = DriveSource(root, {
        "curiosity": {
            "rate_per_hour": 0.4, "threshold": 1.0,
            "budget": {"max_tokens": 3000, "max_actions": 6, "risk_cap": "low"},
            "description": "explore something new",
        },
        "stewardship": {
            "rate_per_hour": 0.25, "threshold": 1.0,
            "budget": {"max_tokens": 2000, "max_actions": 10, "risk_cap": "low"},
            "description": "check on the systems I tend",
        },
    })
    drives.poll(clock())  # birth: drive baselines at T0

    # ── seed a little life into memory ────────────────────────────────
    eid = store.add_episode(
        "manifest job crashed with OOM; restarted with 4GB cap",
        actors=["Christopher"], tags=["manifest", "ops"], ts=T0 - 2 * HOUR)
    store.add_belief("the manifest job OOMs above 8GB input",
                     provenance=[eid], confidence=0.8, ts=T0 - 2 * HOUR)
    timers.at(T0 + 2 * HOUR, "check on the manifest job", now=T0)

    # ── toy echo handler ──────────────────────────────────────────────
    def handler(wake):
        log = ledger.bind(wake, clock=clock)
        pack = orient(store, wake, now=clock(),
                      timer_source=timers, drive_source=drives)
        log("orient", f"context pack: {len(pack)} chars",
            model="local-235b", tokens_in=len(pack) // 4)
        print(f"\n───── wake dispatched: {wake.source} — {wake.reason}")
        print("\n".join("  │ " + l for l in pack.splitlines()[:14]))
        print("  │ …")

        report = {"events": [f"echo-handled {wake.source} wake: {wake.reason}"]}
        if wake.source == "drive":
            name = wake.payload["drive"]
            drives.satisfy(name, now=clock())
            log("drive_satisfy", name)
            report["drive_satisfactions"] = {name: 1.0}
            report["learnings"] = [
                f"drive '{name}' serviced at pressure "
                f"{wake.payload['pressure']:.2f}"]
        elif wake.source == "message":
            log("reply", f"echoed to {wake.payload['sender']}",
                model="local-235b", tokens_out=42)
        return report

    sched = WakeScheduler(store, handler, ledger=ledger, clock=clock,
                          sources=[messages, senses, timers, drives])

    # ── the simulation ────────────────────────────────────────────────
    print(f"entity root: {root}")

    print(f"\n=== tick 1 (T0) — a message and a sense event arrive")
    messages.inject("Christopher", "how is the manifest job going?")
    senses.emit("service-died", {"unit": "vellum-supervisor"}, urgent=True)
    sched.run_pending()

    print(f"\n=== tick 2 (T0+2.5h) — scheduled intention comes due;"
          f" curiosity crosses threshold (0.4/h × 2.5h = 1.0)")
    clock.advance(2.5 * HOUR)
    sched.run_pending()

    print(f"\n=== tick 3 (T0+4.5h) — stewardship crosses threshold"
          f" (0.25/h × 4.5h > 1.0); curiosity still rebuilding")
    clock.advance(2 * HOUR)
    sched.run_pending()

    # ── receipts ──────────────────────────────────────────────────────
    print("\n===== LEDGER =====")
    for a in reversed(ledger.recent(100)):
        print(f"  {a['wake_id'][:22]:<22} {a['kind']:<14} "
              f"[{a['outcome']}] {a['detail'][:60]}")
    stats = ledger.stats()
    print(f"\n  totals: {stats['totals']}")
    print(f"  per source: {stats['per_source']}")

    print("\n===== MEMORY =====")
    for k, v in store.stats().items():
        if k != "db_path":
            print(f"  {k}: {v}")
    print("\n  drive state:")
    for d in drives.pressure_summary(clock()):
        print(f"  - {d['name']}: pressure {d['pressure']:.2f}/"
              f"{d['threshold']:.2f}, wakes so far: {d['total_wakes']}")

    for closer in (timers, drives, ledger, store):
        closer.close()
    shutil.rmtree(root)
    print("\ndemo complete (entity root cleaned up).")


if __name__ == "__main__":
    main()
