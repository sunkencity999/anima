"""The runtime shell — the process that hosts an entity (PHASE5_RUNTIME.md).

- Loads an EntityRoot and (optionally) wires the act phase in.
- Single-writer discipline: a pidfile lock; two runtimes sharing a self
  is corruption, not concurrency.
- Wall-clock scheduler loop on a short tick; senses run in their own
  threads and inject wakes.
- Graceful shutdown is a settle event: SIGTERM/SIGINT → drain pending
  wakes → settle a "shutdown" episode → lineage log entry. The entity
  always knows it went to sleep; forgetting is impossible even across
  death.
- UNgraceful death can no longer eat a message either (2026-08-05
  incident: a wake acknowledged with a 202 died with the process and
  nobody — not the entity, not the page, not the ledger — ever knew).
  Injection persists message wakes; start() replays anything the last
  life left unsettled, in arrival order, stale ones skipped with a
  ledger receipt instead of fired days late.
- Every process gets a boot_id (uuid4): the Observatory watches it to
  tell "still composing" from "died and came back" — the typing
  indicator is not allowed to lie.

CLI:  python3 -m anima.runtime --root <entity_root> [--policy routing.json]
"""

from __future__ import annotations

import json
import os
import signal
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from ..entity import EntityRoot
from ..relationships import AccessContext
from .agent_turn import attach_agent_turn
from .tools import ToolRegistry, default_registry


def _age_str(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OverflowError):
        return False
    except PermissionError:
        return True  # alive, owned by someone else
    return True


class PidLock:
    """Single-writer lock for an entity root. Refuses to acquire when
    the pidfile names a live process; stale pidfiles (dead pid) are
    reclaimed."""

    def __init__(self, entity_root: str):
        self.path = os.path.join(os.path.abspath(entity_root), "runtime.pid")
        self.held = False

    def acquire(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    pid = int(f.read().strip() or "0")
            except (ValueError, OSError):
                pid = 0
            if pid and _pid_alive(pid):
                raise RuntimeError(
                    f"entity root already hosted by live pid {pid} "
                    f"({self.path}); two runtimes sharing a self is "
                    "corruption, not concurrency")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()) + "\n")
        self.held = True

    def release(self) -> None:
        if self.held:
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass
            self.held = False


class RuntimeShell:
    """Host process for one entity root.

    Composition:
        shell = RuntimeShell(root, policy_path=...)   # or router=...
        shell.add_sense("console", ConsoleSense())
        shell.run()                                    # blocks until signal

    Tests drive the lifecycle piecewise: start() → inject_message() →
    run_pending_once() → shutdown().
    """

    def __init__(
        self,
        root: str,
        *,
        policy_path: Optional[str] = None,
        clock=time.time,
        tick_s: float = 0.5,
        registry: Optional[ToolRegistry] = None,
        router: Any = None,
        allow_shell: Optional[bool] = None,
        drives: Optional[Dict[str, dict]] = None,
        graph_extraction: Optional[bool] = None,
    ):
        self.clock = clock
        self.tick_s = tick_s
        self.boot_id = uuid.uuid4().hex  # this life, distinguishable
        self.entity = EntityRoot(root, clock=clock, drives=drives)
        self._stop = threading.Event()
        # Dispatch serialization: senses inject from their own threads,
        # but only one thread at a time may run wakes / touch sqlite.
        self._dispatch_lock = threading.RLock()
        self._lock = PidLock(self.entity.root)
        self._senses: Dict[str, Any] = {}
        self.started = False

        config = self._load_config()
        self.replay_max_age_s = float(
            config.get("wake_replay_max_age_s", 24 * 3600.0))
        if allow_shell is None:
            allow_shell = bool(config.get("allow_shell", False))
        self.registry = registry or default_registry(allow_shell=allow_shell)
        if graph_extraction is None:
            graph_extraction = bool(config.get("graph_extraction", True))
        self.graph_extraction = graph_extraction

        if router is None and policy_path:
            from ..routing import Router, RoutingPolicy
            router = Router(RoutingPolicy.from_file(policy_path),
                            ledger=self.entity.ledger, clock=self.clock)
        self.router = router or self.entity.router
        if self.router is not None:
            attach_agent_turn(self.entity, self.registry,
                              router=self.router)
        # else: EntityRoot's default record-only handler stays in place.

    def _load_config(self) -> dict:
        path = os.path.join(self.entity.identity_dir, "config.json")
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg if isinstance(cfg, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    # ── senses ────────────────────────────────────────────────────────
    def add_sense(self, name: str, sense: Any) -> None:
        """A sense must offer deliver(text, wake) for outbound replies;
        start(shell)/stop() are optional lifecycle hooks."""
        self._senses[name] = sense

    def inject_message(self, sender: str, text: str, *,
                       context: Optional[AccessContext] = None,
                       via: Optional[str] = None):
        """Sense adapters call this: message wake + AccessContext."""
        ctx = context or AccessContext.direct(
            str(sender), channel=via or "chat")
        extra: Dict[str, Any] = {"access_context": ctx.to_dict()}
        if via:
            extra["via"] = via
        # The persist rides the inject (sources.py): serialize it the
        # same way every other cross-thread organ touch is serialized —
        # under the dispatch lock. Sense threads wait their turn;
        # sqlite only ever sees one writer.
        with self._dispatch_lock:
            wake = self.entity.messages.inject(
                str(sender), text, channel=ctx.channel or "chat",
                ts=self.clock(), extra=extra)
        return wake

    def inject_event(self, kind: str, payload: Optional[dict] = None, *,
                     urgent: bool = False, via: Optional[str] = None):
        payload = dict(payload or {})
        if via:
            payload["via"] = via
        return self.entity.senses.emit(
            kind, payload, urgent=urgent, ts=self.clock())

    def _route_replies(self, results: List[dict]) -> None:
        for result in results:
            report = result.get("report") or {}
            replies = report.get("replies") or []
            if not replies:
                continue
            wake = result.get("wake")
            via = (wake.payload or {}).get("via") if wake is not None else None
            sense = self._senses.get(via or "")
            if sense is None and len(self._senses) == 1:
                sense = next(iter(self._senses.values()))
            if sense is None:
                continue
            for text in replies:
                try:
                    sense.deliver(text, wake)
                except Exception:
                    pass  # a broken sense must not kill the loop

    def run_pending_once(self, now: Optional[float] = None) -> List[dict]:
        with self._dispatch_lock:
            results = self.entity.scheduler.run_pending(
                now=now if now is not None else self.clock())
            self._extract_graph_edges(results)
        self._route_replies(results)
        return results

    def _extract_graph_edges(self, results: List[dict]) -> None:
        """Settle-time edge extraction (Phase 7 §2), inside the
        dispatch cycle: single-writer discipline untouched, one
        bounded reflex call per settled wake. Failure is logged and
        swallowed — the organism never dies of bad bookkeeping."""
        if not self.graph_extraction or self.router is None:
            return
        from ..memory.graph_extract import extract_edges_for_wake
        for result in results:
            receipt = result.get("receipt") or {}
            if not result.get("ok") or not receipt.get("episode_ids"):
                continue
            extract_edges_for_wake(
                self.entity.store, self.router,
                result.get("report") or {}, receipt,
                now=self.clock(), ledger=self.entity.ledger)

    # ── lifecycle ─────────────────────────────────────────────────────
    def start(self) -> None:
        if self.started:
            return
        self._lock.acquire()
        self.entity._append_lineage(
            "shell_start", f"runtime shell up (pid {os.getpid()})")
        self._replay_unsettled()
        for sense in self._senses.values():
            start = getattr(sense, "start", None)
            if callable(start):
                start(self)
        self.started = True

    def _replay_unsettled(self) -> None:
        """Resurrect message wakes the previous life never settled.
        Runs before any sense starts: replayed debts are queued ahead
        of whatever this life's visitors bring."""
        now = self.clock()
        with self._dispatch_lock:
            replayed, skipped = self.entity.messages.replay_pending(
                now, max_age_s=self.replay_max_age_s)
            for row in skipped:
                age_h = (now - row["created_ts"]) / 3600.0
                self.entity.ledger.log(
                    row["wake_id"], "replay_skipped",
                    f"unsettled message from {row['sender']} was "
                    f"{age_h:.1f}h old (> replay cap); marked stale, "
                    "not fired", source="message", ts=now,
                    outcome="skipped")
            for wake in replayed:
                self.entity.ledger.log(
                    wake.wake_id, "replay",
                    f"replaying unsettled message from "
                    f"{wake.payload.get('sender', '?')} "
                    f"(injected {_age_str(now - wake.ts)} ago"
                    + (", died mid-turn: possible retry)"
                       if wake.payload.get("maybe_retry") else ")"),
                    source="message", ts=now)

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def run(self) -> None:
        """Blocking wall-clock loop. SIGTERM/SIGINT → graceful shutdown."""
        self.start()
        installed = []
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    prev = signal.signal(sig, lambda *_: self.stop())
                    installed.append((sig, prev))
                except ValueError:
                    pass  # not the main thread; caller owns signals
            while not self._stop.is_set():
                self.run_pending_once()
                self._stop.wait(self.tick_s)
        finally:
            for sig, prev in installed:
                try:
                    signal.signal(sig, prev)
                except ValueError:
                    pass
            self.shutdown()

    def shutdown(self) -> None:
        """Drain, settle the shutdown episode, log lineage, release."""
        if not self.started:
            return
        self.started = False
        try:
            self.run_pending_once()  # drain: current wakes finish + settle
        except Exception:
            pass
        for sense in self._senses.values():
            stop = getattr(sense, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass
        now = self.clock()
        self.entity.settle({
            "wake_id": f"wake-shutdown-{uuid.uuid4().hex[:10]}",
            "ts": now,
            "events": [{
                "summary": "runtime shutdown (graceful): drained pending "
                           "wakes and went to sleep",
                "kind": "event",
                "tags": ["shutdown", "runtime"],
            }],
        })
        self.entity._append_lineage(
            "shell_stop", f"runtime shell down (pid {os.getpid()})")
        self._lock.release()
        self.entity.close()
