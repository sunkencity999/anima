"""WakeScheduler: priority queue over pending wakes + enforced settle
(ARCHITECTURE.md §1). Messages are just one wake source among many;
heartbeats disappear as a concept.

Design notes:
- Priority: message > urgent sense > timer > drive > ambient sense
  (numeric classes in sources.py). Ties break FIFO via a monotonic
  sequence number.
- Coalescing: pending (not yet dispatched) wakes with the same
  (source, key) merge into the earliest one via Wake.coalesce_with().
  A wake with key=None is never coalesced.
- The ONLY dispatch path runs through settle_guard.run_settled — a
  handler cannot skip settlement, return garbage, or crash its way out
  of leaving an episodic record.
- Injectable clock; run(max_wakes=..., now_fn advances) — no wall-clock
  dependence, no sleeps. A real runtime would wrap run_pending() in a
  select/poll loop; that shell is Phase 5.
- Every dispatch writes ledger entries (dispatch + settle outcome) when
  a Ledger is attached, so §6 auditability is structural.
"""

from __future__ import annotations

import heapq
import itertools
import time
from typing import Callable, Optional

from ..memory.store import MemoryStore
from .ledger import Ledger
from .settle_guard import run_settled
from .sources import Wake, WakeSource


class WakeScheduler:
    """Polls sources, queues wakes by priority, dispatches under guard."""

    def __init__(
        self,
        store: MemoryStore,
        handler: Callable[[Wake], Optional[dict]],
        *,
        sources: Optional[list[WakeSource]] = None,
        ledger: Optional[Ledger] = None,
        clock: Callable[[], float] = time.time,
    ):
        self.store = store
        self.handler = handler
        self.sources: list[WakeSource] = list(sources or [])
        self.ledger = ledger
        self.clock = clock
        self._heap: list[tuple[int, int, str]] = []   # (priority, seq, wake_id)
        self._pending: dict[str, Wake] = {}           # wake_id -> Wake
        self._by_key: dict[tuple[str, str], str] = {} # (source, key) -> wake_id
        self._seq = itertools.count()
        self.dispatched: int = 0

    # ── source management ─────────────────────────────────────────────
    def add_source(self, source: WakeSource) -> None:
        self.sources.append(source)

    # ── intake ────────────────────────────────────────────────────────
    def submit(self, wake: Wake) -> str:
        """Queue a wake directly (sources use this via pump)."""
        if wake.key is not None:
            k = (wake.source, wake.key)
            existing_id = self._by_key.get(k)
            if existing_id and existing_id in self._pending:
                self._pending[existing_id].coalesce_with(wake)
                return existing_id
            self._by_key[k] = wake.wake_id
        self._pending[wake.wake_id] = wake
        heapq.heappush(self._heap, (wake.priority, next(self._seq), wake.wake_id))
        return wake.wake_id

    def pump(self, now: Optional[float] = None) -> int:
        """Poll all sources once; returns number of new wakes queued."""
        now = now if now is not None else self.clock()
        n = 0
        for source in self.sources:
            for wake in source.poll(now):
                self.submit(wake)
                n += 1
        return n

    def pending_count(self) -> int:
        return len(self._pending)

    # ── dispatch ──────────────────────────────────────────────────────
    def _pop_next(self) -> Optional[Wake]:
        while self._heap:
            _, _, wake_id = heapq.heappop(self._heap)
            wake = self._pending.pop(wake_id, None)
            if wake is not None:
                if wake.key is not None:
                    self._by_key.pop((wake.source, wake.key), None)
                return wake
        return None

    def run_once(self, now: Optional[float] = None) -> Optional[dict]:
        """Dispatch the highest-priority pending wake through the settle
        guard. Returns the guard result (receipt/report/ok) or None if
        nothing is pending."""
        now = now if now is not None else self.clock()
        wake = self._pop_next()
        if wake is None:
            return None
        if self.ledger is not None:
            self.ledger.log(wake.wake_id, "dispatch", wake.reason,
                            source=wake.source, ts=now)
        result = run_settled(self.store, self.handler, wake, now=now)
        self.dispatched += 1
        # Durability hand-back: a source that persists its wakes
        # (MessageSource) learns the debt is paid — on the scheduler
        # thread, inside the dispatch path, single-writer intact.
        for source in self.sources:
            if source.name == wake.source:
                hook = getattr(source, "on_settled", None)
                if callable(hook):
                    try:
                        hook(wake, now)
                    except Exception:
                        pass  # bookkeeping must not kill the loop
        if self.ledger is not None:
            self.ledger.log(
                wake.wake_id, "settle",
                f"episodes={len(result['receipt']['episode_ids'])}"
                + (f" error={result['error']}" if result["error"] else ""),
                source=wake.source, ts=now,
                outcome="ok" if result["ok"] else "error")
        result["wake"] = wake
        return result

    def run_pending(self, now: Optional[float] = None,
                    max_wakes: int = 100) -> list[dict]:
        """Pump sources once, then dispatch all pending wakes in priority
        order (up to max_wakes). Returns guard results in dispatch order."""
        now = now if now is not None else self.clock()
        self.pump(now)
        results: list[dict] = []
        while len(results) < max_wakes:
            result = self.run_once(now)
            if result is None:
                break
            results.append(result)
        return results
