"""EntityRoot — the whole organism, assembled (ARCHITECTURE.md, top).

"The directory IS the agent," made runnable. Given an entity root dir,
EntityRoot wires every organ built in Phases 1–4:

    memory/        MemoryStore + recall (Phase 1)
    wake/          WakeScheduler + Message/Timer/Drive/Sense sources (Phase 2)
    ledger/        Ledger (Phase 2)
    identity/      routing.json → Router (Phase 3, optional)
                   drives.json → DriveSource config (optional)
                   lineage.log → append-only biography of body changes
    relationships/ RelationshipStore + ACL-enforced recall (Phase 4)

Design notes:
- Injectable clock everywhere; EntityRoot passes its clock down to every
  organ that takes one, so a whole entity can run deterministically.
- wake_message() carries the AccessContext inside the wake payload; the
  default handler derives write scope from it (direct → private/owner,
  group → shared, public → public). A custom handler receives the same
  payload and may do model turns via entity.router.
- lineage.log is append-only text (one `ts | kind | detail` line per
  event): first-init and every runtime-version change are biographical
  events. The current version is tracked in identity/.runtime_version.
- recall() REQUIRES thinking about context: pass an AccessContext for
  walled recall; passing None is explicit single-user mode (warns once,
  via Phase 1).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

from . import __version__ as RUNTIME_VERSION
from .memory.recall import build_context_pack
from .memory.settle import settle as settle_report
from .memory.store import MemoryStore
from .relationships import AccessContext, RelationshipStore
from .relationships.acl import default_scope_for_context
from .wake import (
    DriveSource,
    Ledger,
    MessageSource,
    SenseSource,
    TimerSource,
    Wake,
    WakeScheduler,
)


def _iso_utc(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class EntityRoot:
    """Assemble and run an Anima entity from its root directory."""

    def __init__(
        self,
        root: str,
        *,
        handler: Optional[Callable[[Wake], Optional[dict]]] = None,
        drives: Optional[Dict[str, dict]] = None,
        clock: Callable[[], float] = time.time,
        runtime_version: str = RUNTIME_VERSION,
    ):
        self.root = os.path.abspath(root)
        self.clock = clock
        self.runtime_version = runtime_version
        self.identity_dir = os.path.join(self.root, "identity")
        os.makedirs(self.identity_dir, exist_ok=True)

        # ── organs ────────────────────────────────────────────────────
        self.store = MemoryStore(self.root)
        self.ledger = Ledger(self.root)
        self.relationships = RelationshipStore(self.root, clock=clock)

        self.messages = MessageSource()
        self.timers = TimerSource(self.root)
        self.senses = SenseSource()
        sources: List[Any] = [self.messages, self.timers, self.senses]

        drives_cfg = drives if drives is not None else self._load_drives()
        self.drives: Optional[DriveSource] = None
        if drives_cfg:
            self.drives = DriveSource(self.root, drives_cfg)
            sources.append(self.drives)

        self.handler = handler or self._default_handler
        self.scheduler = WakeScheduler(
            self.store, self.handler, sources=sources,
            ledger=self.ledger, clock=clock,
        )

        self.router = self._load_router()
        self._init_lineage()

    # ── optional identity-file organs ─────────────────────────────────
    def _load_drives(self) -> Optional[Dict[str, dict]]:
        path = os.path.join(self.identity_dir, "drives.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_router(self):
        path = os.path.join(self.identity_dir, "routing.json")
        if not os.path.exists(path):
            return None
        # Imported lazily: routing stays a standalone-usable package and
        # entities without a policy file never touch it.
        from .routing import Router, RoutingPolicy
        policy = RoutingPolicy.from_file(path)
        return Router(policy, ledger=self.ledger, clock=self.clock)

    # ── lineage ───────────────────────────────────────────────────────
    @property
    def lineage_path(self) -> str:
        return os.path.join(self.identity_dir, "lineage.log")

    def _append_lineage(self, kind: str, detail: str) -> None:
        line = f"{_iso_utc(self.clock())} | {kind} | {detail}\n"
        with open(self.lineage_path, "a", encoding="utf-8") as f:
            f.write(line)

    def _init_lineage(self) -> None:
        version_file = os.path.join(self.identity_dir, ".runtime_version")
        previous: Optional[str] = None
        if os.path.exists(version_file):
            with open(version_file, "r", encoding="utf-8") as f:
                previous = f.read().strip() or None
        if previous is None:
            self._append_lineage(
                "init",
                f"entity root initialized (anima runtime {self.runtime_version})")
        elif previous != self.runtime_version:
            self._append_lineage(
                "runtime_change",
                f"runtime {previous} -> {self.runtime_version}")
        if previous != self.runtime_version:
            with open(version_file, "w", encoding="utf-8") as f:
                f.write(self.runtime_version + "\n")

    def lineage(self) -> List[str]:
        if not os.path.exists(self.lineage_path):
            return []
        with open(self.lineage_path, "r", encoding="utf-8") as f:
            return [ln.rstrip("\n") for ln in f if ln.strip()]

    # ── the default handler ───────────────────────────────────────────
    def _default_handler(self, wake: Wake) -> dict:
        """Minimal organism reflex: record what happened, correctly
        scoped for whoever was in the room. Real deployments replace
        this with a model-driven handler (via entity.router)."""
        payload = wake.payload or {}
        ctx = None
        raw_ctx = payload.get("access_context")
        if isinstance(raw_ctx, dict):
            ctx = AccessContext.from_dict(raw_ctx)
        if wake.source == "message":
            sender = payload.get("sender", "unknown")
            scope, owner = default_scope_for_context(ctx, sender)
            text = payload.get("text", "")
            return {
                "events": [{
                    "summary": f"message from {sender}: {text[:160]}",
                    "detail": text,
                    "kind": "message",
                    "actors": [sender],
                    "tags": ["message", payload.get("channel", "chat")],
                    "scope": scope,
                    "owner": owner,
                }],
            }
        return {
            "events": [{
                "summary": f"wake handled ({wake.source}: {wake.reason})",
                "kind": "event",
                "tags": [wake.source],
            }],
        }

    # ── public surface ────────────────────────────────────────────────
    def wake_message(
        self,
        sender_person: str,
        text: str,
        context: Optional[AccessContext] = None,
    ) -> List[dict]:
        """Inject a message wake and run the scheduler to quiescence.
        Returns the settle-guard results (receipt/report/ok per wake).
        Default context: direct with the sender."""
        ctx = context or AccessContext.direct(
            sender_person, channel="chat")
        wake = self.messages.inject(
            sender_person, text,
            channel=ctx.channel or "chat", ts=self.clock())
        wake.payload["access_context"] = ctx.to_dict()
        return self.scheduler.run_pending(now=self.clock())

    def recall(
        self,
        query: str,
        context: Optional[AccessContext] = None,
        **kwargs: Any,
    ) -> str:
        """ACL-walled context pack. context=None is explicit single-user
        mode (Phase 1 behavior, warns once)."""
        kwargs.setdefault("now", self.clock())
        return build_context_pack(
            self.store, query,
            access_context=context,
            relationships=self.relationships,
            **kwargs,
        )

    def settle(self, report: dict) -> dict:
        """Directly settle a wake report (runtime organs use this)."""
        return settle_report(self.store, report)

    def stats(self) -> dict:
        ledger_entries = int(self.ledger.db.execute(
            "SELECT COUNT(*) FROM actions").fetchone()[0])
        out = {
            "root": self.root,
            "runtime_version": self.runtime_version,
            "memory": self.store.stats(),
            "relationships": self.relationships.stats(),
            "wakes_dispatched": self.scheduler.dispatched,
            "wakes_pending": self.scheduler.pending_count(),
            "ledger_entries": ledger_entries,
            "router": bool(self.router),
            "drives": bool(self.drives),
            "lineage_entries": len(self.lineage()),
        }
        return out

    # ── lifecycle ─────────────────────────────────────────────────────
    def close(self) -> None:
        self.store.close()
        self.ledger.close()
        self.relationships.close()
        self.timers.close()
        if self.drives is not None:
            self.drives.close()

    def __enter__(self) -> "EntityRoot":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
