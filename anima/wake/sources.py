"""Wake sources: message / timer / drive / sense (ARCHITECTURE.md §1, §4).

Design notes (beyond spec):
- All sources implement `poll(now) -> list[Wake]`. The scheduler owns the
  clock and passes `now` in; sources never call time.time() themselves,
  so tests and the demo are fully deterministic.
- TimerSource and DriveSource persist to <entity_root>/wake/wake.sqlite —
  scheduled intentions and drive pressure survive restart. Message and
  sense queues are deliberately ephemeral (a lost in-flight message is
  the transport's problem to redeliver, not the scheduler's).
- Recurring timers that fall behind (agent asleep past several periods)
  emit ONE catch-up wake and fast-forward next_ts past `now` — waking up
  to 40 stacked "hourly check" wakes helps nobody.
- Drive pressure accumulates lazily at poll time from rate_per_hour ×
  elapsed. Once a drive crosses threshold it emits exactly one wake and
  latches (pending=1) until satisfy() — no re-firing every poll tick.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Priority classes (lower = more urgent). ARCHITECTURE spec order:
# message > urgent sense > timer > drive. Non-urgent senses are ambient
# telemetry and rank below drives (design decision).
PRIORITY_MESSAGE = 0
PRIORITY_SENSE_URGENT = 1
PRIORITY_TIMER = 2
PRIORITY_DRIVE = 3
PRIORITY_SENSE = 4

DEFAULT_BUDGET = {"max_tokens": 8000, "max_actions": 20, "risk_cap": "normal"}
DEFAULT_DRIVE_BUDGET = {"max_tokens": 4000, "max_actions": 8, "risk_cap": "low"}


def _new_wake_id(source: str) -> str:
    return f"wake-{source}-{uuid.uuid4().hex[:10]}"


@dataclass
class Wake:
    """The unit of waking. Handlers receive exactly this."""

    wake_id: str
    source: str          # message | timer | drive | sense
    reason: str          # human-readable trigger description
    payload: dict = field(default_factory=dict)
    budget: dict = field(default_factory=lambda: dict(DEFAULT_BUDGET))
    priority: int = PRIORITY_MESSAGE
    key: Optional[str] = None   # coalesce key; None = never coalesced
    ts: float = 0.0             # when the wake was raised

    def coalesce_with(self, other: "Wake") -> None:
        """Merge a newer wake of the same (source, key) into this one."""
        merged = self.payload.setdefault("coalesced", [])
        merged.append({"reason": other.reason, "payload": other.payload,
                       "ts": other.ts})
        self.priority = min(self.priority, other.priority)
        self.payload["coalesced_count"] = len(merged)


class WakeSource:
    """Abstract wake source. Subclasses emit Wakes from poll()."""

    name = "source"

    def poll(self, now: float) -> list[Wake]:  # pragma: no cover - interface
        raise NotImplementedError


# ── messages ──────────────────────────────────────────────────────────

class MessageSource(WakeSource):
    """Injectable message queue — chat adapters push, scheduler polls."""

    name = "message"

    def __init__(self) -> None:
        self._queue: list[Wake] = []

    def inject(self, sender: str, text: str, *, channel: str = "chat",
               key: Optional[str] = None, ts: float = 0.0) -> Wake:
        wake = Wake(
            wake_id=_new_wake_id("msg"),
            source="message",
            reason=f"message from {sender}",
            payload={"sender": sender, "text": text, "channel": channel},
            budget=dict(DEFAULT_BUDGET),
            priority=PRIORITY_MESSAGE,
            key=key or f"{channel}:{sender}",
            ts=ts,
        )
        self._queue.append(wake)
        return wake

    def poll(self, now: float) -> list[Wake]:
        out, self._queue = self._queue, []
        for w in out:
            if not w.ts:
                w.ts = now
        return out


# ── senses ────────────────────────────────────────────────────────────

class SenseSource(WakeSource):
    """Generic external-event injection: presence, service-died, camera…"""

    name = "sense"

    def __init__(self) -> None:
        self._queue: list[Wake] = []

    def emit(self, kind: str, payload: Optional[dict] = None, *,
             urgent: bool = False, key: Optional[str] = None,
             ts: float = 0.0) -> Wake:
        wake = Wake(
            wake_id=_new_wake_id("sense"),
            source="sense",
            reason=f"sense event: {kind}" + (" (urgent)" if urgent else ""),
            payload={"kind": kind, "urgent": urgent, **(payload or {})},
            budget=dict(DEFAULT_BUDGET),
            priority=PRIORITY_SENSE_URGENT if urgent else PRIORITY_SENSE,
            key=key or kind,
            ts=ts,
        )
        self._queue.append(wake)
        return wake

    def poll(self, now: float) -> list[Wake]:
        out, self._queue = self._queue, []
        for w in out:
            if not w.ts:
                w.ts = now
        return out


# ── shared sqlite for persisted sources ───────────────────────────────

_WAKE_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS timers (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    kind     TEXT NOT NULL,              -- 'at' | 'every'
    next_ts  REAL NOT NULL,
    interval REAL,                       -- seconds, recurring only
    reason   TEXT NOT NULL,
    payload  TEXT NOT NULL DEFAULT '{}',
    key      TEXT,
    active   INTEGER NOT NULL DEFAULT 1,
    created_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_timers_due ON timers(active, next_ts);

CREATE TABLE IF NOT EXISTS drive_state (
    name       TEXT PRIMARY KEY,
    pressure   REAL NOT NULL DEFAULT 0.0,
    updated_ts REAL NOT NULL,
    pending    INTEGER NOT NULL DEFAULT 0,
    total_wakes INTEGER NOT NULL DEFAULT 0
);
"""


def _open_wake_db(entity_root: str) -> sqlite3.Connection:
    wake_dir = os.path.join(os.path.abspath(entity_root), "wake")
    os.makedirs(wake_dir, exist_ok=True)
    db = sqlite3.connect(os.path.join(wake_dir, "wake.sqlite"),
                         check_same_thread=False)  # shell serializes (Phase 5)
    db.row_factory = sqlite3.Row
    db.executescript(_WAKE_SCHEMA)
    db.commit()
    return db


# ── timers ────────────────────────────────────────────────────────────

class TimerSource(WakeSource):
    """One-shot and recurring scheduled intentions, persisted in sqlite."""

    name = "timer"

    def __init__(self, entity_root: str):
        self.db = _open_wake_db(entity_root)

    def close(self) -> None:
        self.db.close()

    def at(self, when_ts: float, reason: str, payload: Optional[dict] = None,
           *, key: Optional[str] = None, now: float = 0.0) -> int:
        """Schedule a one-shot wake at when_ts."""
        cur = self.db.execute(
            "INSERT INTO timers (kind, next_ts, reason, payload, key, created_ts)"
            " VALUES ('at', ?, ?, ?, ?, ?)",
            (when_ts, reason, json.dumps(payload or {}), key, now),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def every(self, interval_seconds: float, reason: str,
              payload: Optional[dict] = None, *, key: Optional[str] = None,
              start_ts: Optional[float] = None, now: float = 0.0) -> int:
        """Schedule a recurring wake every interval_seconds."""
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be > 0")
        first = start_ts if start_ts is not None else now + interval_seconds
        cur = self.db.execute(
            "INSERT INTO timers (kind, next_ts, interval, reason, payload, key,"
            " created_ts) VALUES ('every', ?, ?, ?, ?, ?, ?)",
            (first, interval_seconds, reason, json.dumps(payload or {}), key, now),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def cancel(self, timer_id: int) -> None:
        self.db.execute("UPDATE timers SET active=0 WHERE id=?", (timer_id,))
        self.db.commit()

    def open_intentions(self, now: Optional[float] = None) -> list[dict]:
        """Pending scheduled intentions — feeds the orient pack."""
        rows = self.db.execute(
            "SELECT * FROM timers WHERE active=1 ORDER BY next_ts"
        ).fetchall()
        return [
            {
                "id": r["id"], "kind": r["kind"], "next_ts": r["next_ts"],
                "interval": r["interval"], "reason": r["reason"],
                "payload": json.loads(r["payload"] or "{}"), "key": r["key"],
            }
            for r in rows
        ]

    def poll(self, now: float) -> list[Wake]:
        due = self.db.execute(
            "SELECT * FROM timers WHERE active=1 AND next_ts <= ?"
            " ORDER BY next_ts",
            (now,),
        ).fetchall()
        wakes: list[Wake] = []
        for r in due:
            payload = json.loads(r["payload"] or "{}")
            payload["timer_id"] = r["id"]
            payload["scheduled_ts"] = r["next_ts"]
            wakes.append(Wake(
                wake_id=_new_wake_id("timer"),
                source="timer",
                reason=r["reason"],
                payload=payload,
                budget=dict(DEFAULT_BUDGET),
                priority=PRIORITY_TIMER,
                key=r["key"] or f"timer:{r['id']}",
                ts=now,
            ))
            if r["kind"] == "at":
                self.db.execute(
                    "UPDATE timers SET active=0 WHERE id=?", (r["id"],))
            else:
                # Catch up past `now` in one jump: one wake per poll, not
                # one per missed period.
                nxt = r["next_ts"]
                interval = float(r["interval"])
                while nxt <= now:
                    nxt += interval
                self.db.execute(
                    "UPDATE timers SET next_ts=? WHERE id=?", (nxt, r["id"]))
        if due:
            self.db.commit()
        return wakes


# ── drives ────────────────────────────────────────────────────────────

class DriveSource(WakeSource):
    """Drives as scheduler input (§4): pressure accumulates over time;
    crossing threshold emits a budgeted wake; satisfaction resets.

    Drive config (drives.yaml-style dict):
        {
          "curiosity": {
            "rate_per_hour": 0.2,     # pressure gained per hour
            "threshold": 1.0,          # wake when pressure >= this
            "budget": {"max_tokens": 4000, "max_actions": 8,
                       "risk_cap": "low"},
            "description": "explore something new",
          },
          ...
        }
    State (pressure, latch, wake count) persists in sqlite.
    """

    name = "drive"

    def __init__(self, entity_root: str, drives: dict[str, dict]):
        self.db = _open_wake_db(entity_root)
        self.drives = {}
        for name, cfg in drives.items():
            self.drives[name] = {
                "rate_per_hour": float(cfg.get("rate_per_hour", 0.1)),
                "threshold": float(cfg.get("threshold", 1.0)),
                "budget": {**DEFAULT_DRIVE_BUDGET, **(cfg.get("budget") or {})},
                "description": cfg.get("description", ""),
            }

    def close(self) -> None:
        self.db.close()

    def _row(self, name: str) -> Optional[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM drive_state WHERE name=?", (name,)).fetchone()

    def _ensure_row(self, name: str, now: float) -> sqlite3.Row:
        row = self._row(name)
        if row is None:
            self.db.execute(
                "INSERT INTO drive_state (name, pressure, updated_ts)"
                " VALUES (?, 0.0, ?)", (name, now))
            self.db.commit()
            row = self._row(name)
        return row  # type: ignore[return-value]

    def _accumulate(self, name: str, now: float) -> sqlite3.Row:
        cfg = self.drives[name]
        row = self._ensure_row(name, now)
        elapsed_h = max(0.0, (now - row["updated_ts"]) / 3600.0)
        pressure = row["pressure"] + cfg["rate_per_hour"] * elapsed_h
        self.db.execute(
            "UPDATE drive_state SET pressure=?, updated_ts=? WHERE name=?",
            (pressure, now, name))
        self.db.commit()
        return self._row(name)  # type: ignore[return-value]

    def poll(self, now: float) -> list[Wake]:
        wakes: list[Wake] = []
        for name, cfg in self.drives.items():
            row = self._accumulate(name, now)
            if row["pressure"] >= cfg["threshold"] and not row["pending"]:
                self.db.execute(
                    "UPDATE drive_state SET pending=1,"
                    " total_wakes=total_wakes+1 WHERE name=?", (name,))
                self.db.commit()
                wakes.append(Wake(
                    wake_id=_new_wake_id("drive"),
                    source="drive",
                    reason=f"drive '{name}' crossed threshold"
                           + (f": {cfg['description']}" if cfg["description"] else ""),
                    payload={"drive": name, "pressure": row["pressure"],
                             "threshold": cfg["threshold"]},
                    budget=dict(cfg["budget"]),
                    priority=PRIORITY_DRIVE,
                    key=f"drive:{name}",
                    ts=now,
                ))
        return wakes

    def satisfy(self, name: str, amount: Optional[float] = None,
                *, now: float = 0.0) -> float:
        """Satisfy a drive: reset (or reduce) pressure, release the latch.
        Returns the new pressure."""
        if name not in self.drives:
            raise KeyError(f"unknown drive {name!r}")
        row = self._ensure_row(name, now)
        new_pressure = 0.0 if amount is None else max(0.0, row["pressure"] - amount)
        pending = 1 if (amount is not None
                        and new_pressure >= self.drives[name]["threshold"]) else 0
        self.db.execute(
            "UPDATE drive_state SET pressure=?, pending=?, updated_ts=?"
            " WHERE name=?",
            (new_pressure, pending, now if now else row["updated_ts"], name))
        self.db.commit()
        return new_pressure

    def pressure_summary(self, now: float) -> list[dict]:
        """Current pressure per drive — feeds the orient pack."""
        out = []
        for name, cfg in self.drives.items():
            row = self._accumulate(name, now)
            out.append({
                "name": name,
                "pressure": row["pressure"],
                "threshold": cfg["threshold"],
                "fraction": (row["pressure"] / cfg["threshold"])
                            if cfg["threshold"] else 0.0,
                "pending": bool(row["pending"]),
                "total_wakes": row["total_wakes"],
                "description": cfg["description"],
            })
        return out
