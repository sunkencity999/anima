"""SQLite-backed entity memory store: episodic / semantic / procedural.

Design notes (beyond spec):
- One sqlite file per entity at <entity_root>/memory/memory.sqlite. The
  entity root directory IS the agent (ARCHITECTURE.md); the store never
  reaches outside it.
- Episodic layer is append-only by convention: no update/delete API is
  exposed. FTS5 external-content index kept in sync via triggers.
- All timestamps are Unix epoch floats (UTC). Human-readable ISO strings
  are derived at render time (recall.py), never stored as truth.
- JSON columns (actors, tags, provenance, known_failure_modes) are stored
  as JSON text; helpers encode/decode transparently.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from typing import Any, Iterable, Optional

SCHEMA_VERSION = 2

# Scope whitelist duplicated from anima.relationships.acl to keep the
# store importable standalone; the two MUST stay in sync (tested).
KNOWN_SCOPES = ("private", "household", "shared", "public")
DEFAULT_SCOPE = "shared"

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ── episodic: append-only experience log ─────────────────────────────
CREATE TABLE IF NOT EXISTS episodic (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    wake_id TEXT,
    kind    TEXT NOT NULL DEFAULT 'event',
    actors  TEXT NOT NULL DEFAULT '[]',   -- JSON list of names
    summary TEXT NOT NULL,
    detail  TEXT NOT NULL DEFAULT '',
    tags    TEXT NOT NULL DEFAULT '[]',   -- JSON list
    scope   TEXT NOT NULL DEFAULT 'shared',
    owner_person_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_episodic_ts ON episodic(ts);
CREATE INDEX IF NOT EXISTS idx_episodic_wake ON episodic(wake_id);

CREATE VIRTUAL TABLE IF NOT EXISTS episodic_fts USING fts5(
    summary, detail, tags, actors,
    content='episodic', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS episodic_ai AFTER INSERT ON episodic BEGIN
    INSERT INTO episodic_fts(rowid, summary, detail, tags, actors)
    VALUES (new.id, new.summary, new.detail, new.tags, new.actors);
END;

-- ── semantic: beliefs with provenance + confidence + lifecycle ───────
CREATE TABLE IF NOT EXISTS semantic (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    statement      TEXT NOT NULL,
    provenance     TEXT NOT NULL DEFAULT '[]',  -- JSON list of episode ids
    confidence     REAL NOT NULL DEFAULT 0.6,
    created_ts     REAL NOT NULL,
    last_confirmed REAL NOT NULL,
    status         TEXT NOT NULL DEFAULT 'active',  -- active|stale|contradicted
    tags           TEXT NOT NULL DEFAULT '[]',
    superseded_by  INTEGER REFERENCES semantic(id),
    scope          TEXT NOT NULL DEFAULT 'shared',
    owner_person_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_semantic_status ON semantic(status);

CREATE VIRTUAL TABLE IF NOT EXISTS semantic_fts USING fts5(
    statement, tags,
    content='semantic', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS semantic_ai AFTER INSERT ON semantic BEGIN
    INSERT INTO semantic_fts(rowid, statement, tags)
    VALUES (new.id, new.statement, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS semantic_au AFTER UPDATE ON semantic BEGIN
    INSERT INTO semantic_fts(semantic_fts, rowid, statement, tags)
    VALUES ('delete', old.id, old.statement, old.tags);
    INSERT INTO semantic_fts(rowid, statement, tags)
    VALUES (new.id, new.statement, new.tags);
END;

-- ── procedural: skills with telemetry ────────────────────────────────
CREATE TABLE IF NOT EXISTS procedural (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL UNIQUE,
    description         TEXT NOT NULL DEFAULT '',
    recipe              TEXT NOT NULL DEFAULT '',
    success_count       INTEGER NOT NULL DEFAULT 0,
    failure_count       INTEGER NOT NULL DEFAULT 0,
    last_worked         REAL,
    known_failure_modes TEXT NOT NULL DEFAULT '[]',  -- JSON list
    created_ts          REAL NOT NULL,
    tags                TEXT NOT NULL DEFAULT '[]',
    scope               TEXT NOT NULL DEFAULT 'shared',
    owner_person_id     TEXT
);

-- ── consolidation queue: settle-phase → background organ handoff ─────
CREATE TABLE IF NOT EXISTS consolidation_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    wake_id     TEXT,
    candidate   TEXT NOT NULL,               -- proposed belief text
    episode_ids TEXT NOT NULL DEFAULT '[]',  -- JSON provenance
    hint        TEXT NOT NULL DEFAULT 'learning',
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending|done|rejected
    resolution  TEXT
);
CREATE INDEX IF NOT EXISTS idx_cq_status ON consolidation_queue(status);
"""


def _j(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)


def _uj(text: Optional[str]) -> Any:
    if not text:
        return []
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return []


_FTS_TOKEN = re.compile(r"[A-Za-z0-9_]+")


def fts_sanitize(query: str) -> str:
    """Turn arbitrary text into a safe FTS5 OR-query of bare tokens."""
    tokens = _FTS_TOKEN.findall(query or "")
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens)


def _validate_scope(scope: str) -> str:
    if scope not in KNOWN_SCOPES:
        raise ValueError(
            f"unknown memory scope {scope!r}; known: {KNOWN_SCOPES}")
    return scope


def _acl_where(acl, prefix: str = ""):
    """Duck-typed ACL hook: anything with .where(prefix) -> (sql, params).

    Returns (" AND <fragment>", params) ready to splice into a query,
    or ("", []) when acl is None (single-user mode — no filtering).
    The fragment is compiled by anima.relationships.acl; the store never
    imports that package, so Phase 1 stays standalone.
    """
    if acl is None:
        return "", []
    sql, params = acl.where(prefix)
    return f" AND {sql}", list(params)


class MemoryStore:
    """Three-layer memory store rooted at an entity directory."""

    def __init__(self, entity_root: str):
        self.entity_root = os.path.abspath(entity_root)
        self.memory_dir = os.path.join(self.entity_root, "memory")
        os.makedirs(self.memory_dir, exist_ok=True)
        self.db_path = os.path.join(self.memory_dir, "memory.sqlite")
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self._migrate()
        self.db.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.db.commit()

    def _migrate(self) -> None:
        """v1 → v2: episodic/semantic/procedural gain scope + owner
        columns with defaults, so pre-Phase-4 entity roots open cleanly
        and every legacy row is 'shared' (matches old behavior)."""
        for table in ("episodic", "semantic", "procedural"):
            cols = {r[1] for r in self.db.execute(
                f"PRAGMA table_info({table})").fetchall()}
            if "scope" not in cols:
                self.db.execute(
                    f"ALTER TABLE {table} ADD COLUMN scope TEXT NOT NULL"
                    " DEFAULT 'shared'")
            if "owner_person_id" not in cols:
                self.db.execute(
                    f"ALTER TABLE {table} ADD COLUMN owner_person_id TEXT")
        self.db.commit()

    # ── lifecycle ─────────────────────────────────────────────────────
    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── episodic ──────────────────────────────────────────────────────
    def add_episode(
        self,
        summary: str,
        detail: str = "",
        kind: str = "event",
        actors: Optional[Iterable[str]] = None,
        tags: Optional[Iterable[str]] = None,
        wake_id: Optional[str] = None,
        ts: Optional[float] = None,
        scope: str = DEFAULT_SCOPE,
        owner: Optional[str] = None,
    ) -> int:
        _validate_scope(scope)
        cur = self.db.execute(
            "INSERT INTO episodic (ts, wake_id, kind, actors, summary, detail,"
            " tags, scope, owner_person_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                ts if ts is not None else time.time(),
                wake_id,
                kind,
                _j(list(actors) if actors else []),
                summary,
                detail,
                _j(list(tags) if tags else []),
                scope,
                owner,
            ),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def get_episode(self, episode_id: int) -> Optional[dict]:
        row = self.db.execute(
            "SELECT * FROM episodic WHERE id=?", (episode_id,)
        ).fetchone()
        return self._episode_row(row) if row else None

    def search_episodes(self, query: str, limit: int = 20,
                        acl=None) -> list[dict]:
        """FTS5 search; returns episodes with bm25 score (lower = better).
        When acl is provided, its WHERE fragment is applied INSIDE the
        SQL query — unauthorized rows never leave sqlite."""
        match = fts_sanitize(query)
        if not match:
            return []
        acl_sql, acl_params = _acl_where(acl, "e.")
        rows = self.db.execute(
            "SELECT e.*, bm25(episodic_fts) AS score"
            " FROM episodic_fts JOIN episodic e ON e.id = episodic_fts.rowid"
            f" WHERE episodic_fts MATCH ?{acl_sql} ORDER BY score LIMIT ?",
            (match, *acl_params, limit),
        ).fetchall()
        out = []
        for row in rows:
            ep = self._episode_row(row)
            ep["score"] = row["score"]
            out.append(ep)
        return out

    def recent_episodes(self, limit: int = 20, acl=None) -> list[dict]:
        acl_sql, acl_params = _acl_where(acl)
        rows = self.db.execute(
            f"SELECT * FROM episodic WHERE 1=1{acl_sql}"
            " ORDER BY ts DESC LIMIT ?",
            (*acl_params, limit),
        ).fetchall()
        return [self._episode_row(r) for r in rows]

    @staticmethod
    def _episode_row(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "ts": row["ts"],
            "wake_id": row["wake_id"],
            "kind": row["kind"],
            "actors": _uj(row["actors"]),
            "summary": row["summary"],
            "detail": row["detail"],
            "tags": _uj(row["tags"]),
            "scope": row["scope"],
            "owner_person_id": row["owner_person_id"],
        }

    # ── semantic ──────────────────────────────────────────────────────
    def add_belief(
        self,
        statement: str,
        provenance: Optional[Iterable[int]] = None,
        confidence: float = 0.6,
        tags: Optional[Iterable[str]] = None,
        ts: Optional[float] = None,
        scope: str = DEFAULT_SCOPE,
        owner: Optional[str] = None,
    ) -> int:
        _validate_scope(scope)
        now = ts if ts is not None else time.time()
        cur = self.db.execute(
            "INSERT INTO semantic (statement, provenance, confidence, created_ts,"
            " last_confirmed, status, tags, scope, owner_person_id)"
            " VALUES (?,?,?,?,?,'active',?,?,?)",
            (
                statement,
                _j(sorted(set(int(i) for i in (provenance or [])))),
                max(0.0, min(1.0, confidence)),
                now,
                now,
                _j(list(tags) if tags else []),
                scope,
                owner,
            ),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def get_belief(self, belief_id: int) -> Optional[dict]:
        row = self.db.execute(
            "SELECT * FROM semantic WHERE id=?", (belief_id,)
        ).fetchone()
        return self._belief_row(row) if row else None

    def confirm_belief(
        self,
        belief_id: int,
        episode_ids: Optional[Iterable[int]] = None,
        confidence_bump: float = 0.1,
        ts: Optional[float] = None,
    ) -> None:
        """Re-confirm a belief: refresh last_confirmed, merge provenance,
        bump confidence, and revive 'stale' back to 'active'."""
        belief = self.get_belief(belief_id)
        if belief is None:
            raise KeyError(f"no belief {belief_id}")
        prov = set(belief["provenance"]) | set(int(i) for i in (episode_ids or []))
        new_conf = max(0.0, min(1.0, belief["confidence"] + confidence_bump))
        new_status = "active" if belief["status"] == "stale" else belief["status"]
        self.db.execute(
            "UPDATE semantic SET provenance=?, confidence=?, last_confirmed=?,"
            " status=? WHERE id=?",
            (_j(sorted(prov)), new_conf, ts if ts is not None else time.time(),
             new_status, belief_id),
        )
        self.db.commit()

    def contradict_belief(
        self,
        belief_id: int,
        superseded_by: Optional[int] = None,
    ) -> None:
        self.db.execute(
            "UPDATE semantic SET status='contradicted', superseded_by=? WHERE id=?",
            (superseded_by, belief_id),
        )
        self.db.commit()

    def search_beliefs(
        self, query: str, limit: int = 20, include_inactive: bool = False,
        acl=None,
    ) -> list[dict]:
        match = fts_sanitize(query)
        if not match:
            return []
        status_clause = "" if include_inactive else " AND s.status != 'contradicted'"
        acl_sql, acl_params = _acl_where(acl, "s.")
        rows = self.db.execute(
            "SELECT s.*, bm25(semantic_fts) AS score"
            " FROM semantic_fts JOIN semantic s ON s.id = semantic_fts.rowid"
            f" WHERE semantic_fts MATCH ?{status_clause}{acl_sql}"
            " ORDER BY score LIMIT ?",
            (match, *acl_params, limit),
        ).fetchall()
        out = []
        for row in rows:
            b = self._belief_row(row)
            b["score"] = row["score"]
            out.append(b)
        return out

    def flag_stale_beliefs(
        self, max_age_days: float, now: Optional[float] = None
    ) -> list[dict]:
        """Staleness decay: mark active beliefs unconfirmed for more than
        max_age_days as 'stale' and return them for re-verification."""
        cutoff = (now if now is not None else time.time()) - max_age_days * 86400
        rows = self.db.execute(
            "SELECT * FROM semantic WHERE status='active' AND last_confirmed < ?",
            (cutoff,),
        ).fetchall()
        flagged = [self._belief_row(r) for r in rows]
        if flagged:
            self.db.executemany(
                "UPDATE semantic SET status='stale' WHERE id=?",
                [(b["id"],) for b in flagged],
            )
            self.db.commit()
        for b in flagged:
            b["status"] = "stale"
        return flagged

    def list_beliefs(self, status: Optional[str] = None) -> list[dict]:
        if status:
            rows = self.db.execute(
                "SELECT * FROM semantic WHERE status=? ORDER BY id", (status,)
            ).fetchall()
        else:
            rows = self.db.execute("SELECT * FROM semantic ORDER BY id").fetchall()
        return [self._belief_row(r) for r in rows]

    @staticmethod
    def _belief_row(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "statement": row["statement"],
            "provenance": _uj(row["provenance"]),
            "confidence": row["confidence"],
            "created_ts": row["created_ts"],
            "last_confirmed": row["last_confirmed"],
            "status": row["status"],
            "tags": _uj(row["tags"]),
            "superseded_by": row["superseded_by"],
            "scope": row["scope"],
            "owner_person_id": row["owner_person_id"],
        }

    # ── procedural ────────────────────────────────────────────────────
    def add_skill(
        self,
        name: str,
        description: str = "",
        recipe: str = "",
        tags: Optional[Iterable[str]] = None,
        ts: Optional[float] = None,
        scope: str = DEFAULT_SCOPE,
        owner: Optional[str] = None,
    ) -> int:
        _validate_scope(scope)
        cur = self.db.execute(
            "INSERT INTO procedural (name, description, recipe, created_ts,"
            " tags, scope, owner_person_id) VALUES (?,?,?,?,?,?,?)",
            (name, description, recipe,
             ts if ts is not None else time.time(),
             _j(list(tags) if tags else []), scope, owner),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def get_skill(self, name: str) -> Optional[dict]:
        row = self.db.execute(
            "SELECT * FROM procedural WHERE name=?", (name,)
        ).fetchone()
        return self._skill_row(row) if row else None

    def record_skill_outcome(
        self,
        name: str,
        success: bool,
        failure_mode: Optional[str] = None,
        ts: Optional[float] = None,
    ) -> dict:
        skill = self.get_skill(name)
        if skill is None:
            raise KeyError(f"no skill named {name!r}")
        now = ts if ts is not None else time.time()
        if success:
            self.db.execute(
                "UPDATE procedural SET success_count=success_count+1, last_worked=?"
                " WHERE name=?",
                (now, name),
            )
        else:
            modes = skill["known_failure_modes"]
            if failure_mode and failure_mode not in modes:
                modes.append(failure_mode)
            self.db.execute(
                "UPDATE procedural SET failure_count=failure_count+1,"
                " known_failure_modes=? WHERE name=?",
                (_j(modes), name),
            )
        self.db.commit()
        return self.get_skill(name)  # type: ignore[return-value]

    def list_skills(self, acl=None) -> list[dict]:
        acl_sql, acl_params = _acl_where(acl)
        rows = self.db.execute(
            f"SELECT * FROM procedural WHERE 1=1{acl_sql} ORDER BY name",
            acl_params,
        ).fetchall()
        return [self._skill_row(r) for r in rows]

    @staticmethod
    def _skill_row(row: sqlite3.Row) -> dict:
        total = row["success_count"] + row["failure_count"]
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "recipe": row["recipe"],
            "success_count": row["success_count"],
            "failure_count": row["failure_count"],
            "success_rate": (row["success_count"] / total) if total else None,
            "last_worked": row["last_worked"],
            "known_failure_modes": _uj(row["known_failure_modes"]),
            "created_ts": row["created_ts"],
            "tags": _uj(row["tags"]),
            "scope": row["scope"],
            "owner_person_id": row["owner_person_id"],
        }

    # ── consolidation queue ───────────────────────────────────────────
    def queue_candidate(
        self,
        candidate: str,
        episode_ids: Optional[Iterable[int]] = None,
        hint: str = "learning",
        wake_id: Optional[str] = None,
        ts: Optional[float] = None,
    ) -> int:
        cur = self.db.execute(
            "INSERT INTO consolidation_queue (ts, wake_id, candidate, episode_ids, hint)"
            " VALUES (?,?,?,?,?)",
            (ts if ts is not None else time.time(), wake_id, candidate,
             _j(sorted(set(int(i) for i in (episode_ids or [])))), hint),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def pending_candidates(self, limit: int = 100) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM consolidation_queue WHERE status='pending'"
            " ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "ts": r["ts"],
                "wake_id": r["wake_id"],
                "candidate": r["candidate"],
                "episode_ids": _uj(r["episode_ids"]),
                "hint": r["hint"],
                "status": r["status"],
                "resolution": r["resolution"],
            }
            for r in rows
        ]

    def resolve_candidate(self, candidate_id: int, resolution: str,
                          status: str = "done") -> None:
        self.db.execute(
            "UPDATE consolidation_queue SET status=?, resolution=? WHERE id=?",
            (status, resolution, candidate_id),
        )
        self.db.commit()

    # ── stats ─────────────────────────────────────────────────────────
    def stats(self) -> dict:
        def count(sql: str, *args) -> int:
            return int(self.db.execute(sql, args).fetchone()[0])

        return {
            "db_path": self.db_path,
            "episodes": count("SELECT COUNT(*) FROM episodic"),
            "beliefs": {
                "active": count("SELECT COUNT(*) FROM semantic WHERE status='active'"),
                "stale": count("SELECT COUNT(*) FROM semantic WHERE status='stale'"),
                "contradicted": count(
                    "SELECT COUNT(*) FROM semantic WHERE status='contradicted'"),
            },
            "skills": count("SELECT COUNT(*) FROM procedural"),
            "consolidation_pending": count(
                "SELECT COUNT(*) FROM consolidation_queue WHERE status='pending'"),
        }
