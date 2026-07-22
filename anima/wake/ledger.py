"""Append-only action ledger (ARCHITECTURE.md §6).

Everything the agent does writes here: what, why (wake_id), which model,
cost. From this you get honest self-audit, cost analytics, post-mortems
with receipts, and the trust substrate.

Design notes:
- Lives at <entity_root>/ledger/ledger.sqlite — its own organ directory
  per the entity-root spec, separate from memory.sqlite.
- Append-only by convention AND surface: no update/delete API exists.
- `bind(wake)` returns a tiny logger closure so handlers can record
  actions without carrying wake_id/plumbing around.
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Callable, Optional

_LEDGER_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS actions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    wake_id    TEXT NOT NULL,
    ts         REAL NOT NULL,
    source     TEXT NOT NULL DEFAULT '',   -- wake source (message/timer/…)
    kind       TEXT NOT NULL,              -- e.g. tool_call, reply, dispatch
    detail     TEXT NOT NULL DEFAULT '',
    model      TEXT,
    tokens_in  INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd   REAL NOT NULL DEFAULT 0.0,
    outcome    TEXT NOT NULL DEFAULT 'ok'  -- ok | error | skipped
);
CREATE INDEX IF NOT EXISTS idx_actions_wake ON actions(wake_id);
CREATE INDEX IF NOT EXISTS idx_actions_ts ON actions(ts);
"""


class Ledger:
    """Append-only action ledger rooted at an entity directory."""

    def __init__(self, entity_root: str):
        self.entity_root = os.path.abspath(entity_root)
        ledger_dir = os.path.join(self.entity_root, "ledger")
        os.makedirs(ledger_dir, exist_ok=True)
        self.db_path = os.path.join(ledger_dir, "ledger.sqlite")
        self.db = sqlite3.connect(self.db_path, check_same_thread=False)  # shell serializes cross-thread access (Phase 5)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_LEDGER_SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── append ────────────────────────────────────────────────────────
    def log(
        self,
        wake_id: str,
        kind: str,
        detail: str = "",
        *,
        source: str = "",
        model: Optional[str] = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
        outcome: str = "ok",
        ts: Optional[float] = None,
    ) -> int:
        cur = self.db.execute(
            "INSERT INTO actions (wake_id, ts, source, kind, detail, model,"
            " tokens_in, tokens_out, cost_usd, outcome)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (wake_id, ts if ts is not None else time.time(), source, kind,
             detail, model, tokens_in, tokens_out, cost_usd, outcome),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def bind(self, wake, *, clock: Optional[Callable[[], float]] = None):
        """Return log_action(kind, detail, **fields) pre-bound to a wake."""
        def log_action(kind: str, detail: str = "", **fields) -> int:
            if clock is not None and "ts" not in fields:
                fields["ts"] = clock()
            return self.log(wake.wake_id, kind, detail,
                            source=wake.source, **fields)
        return log_action

    # ── read / audit ──────────────────────────────────────────────────
    def for_wake(self, wake_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM actions WHERE wake_id=? ORDER BY id", (wake_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def recent(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def window(self, until_ts: float, limit: int = 50) -> list[dict]:
        """The `limit` most recent actions at or before `until_ts`,
        newest-first — time travel reads (Observatory v3). Read-only,
        windowed: the whole ledger never ships anywhere."""
        rows = self.db.execute(
            "SELECT * FROM actions WHERE ts <= ? ORDER BY id DESC LIMIT ?",
            (until_ts, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def bounds(self) -> dict:
        """Timeline extent: oldest/newest action ts + row count."""
        row = self.db.execute(
            "SELECT MIN(ts) AS oldest, MAX(ts) AS newest,"
            " COUNT(*) AS actions FROM actions").fetchone()
        return {"oldest": row["oldest"], "newest": row["newest"],
                "actions": row["actions"] or 0}

    def stats(self) -> dict:
        """Audit rollups: actions per day, per wake source, per kind,
        plus token/cost totals."""
        per_day = {
            r["day"]: r["n"] for r in self.db.execute(
                "SELECT date(ts, 'unixepoch') AS day, COUNT(*) AS n"
                " FROM actions GROUP BY day ORDER BY day")
        }
        per_source = {
            (r["source"] or "(none)"): r["n"] for r in self.db.execute(
                "SELECT source, COUNT(*) AS n FROM actions"
                " GROUP BY source ORDER BY n DESC")
        }
        per_kind = {
            r["kind"]: r["n"] for r in self.db.execute(
                "SELECT kind, COUNT(*) AS n FROM actions"
                " GROUP BY kind ORDER BY n DESC")
        }
        totals = self.db.execute(
            "SELECT COUNT(*) AS actions, COUNT(DISTINCT wake_id) AS wakes,"
            " SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out,"
            " SUM(cost_usd) AS cost_usd,"
            " SUM(CASE WHEN outcome='error' THEN 1 ELSE 0 END) AS errors"
            " FROM actions").fetchone()
        return {
            "db_path": self.db_path,
            "totals": {
                "actions": totals["actions"] or 0,
                "wakes": totals["wakes"] or 0,
                "tokens_in": totals["tokens_in"] or 0,
                "tokens_out": totals["tokens_out"] or 0,
                "cost_usd": round(totals["cost_usd"] or 0.0, 6),
                "errors": totals["errors"] or 0,
            },
            "per_day": per_day,
            "per_source": per_source,
            "per_kind": per_kind,
        }
