"""Per-person relationship records (ARCHITECTURE.md §5).

Design notes:
- Truth lives in sqlite at <entity_root>/relationships/relationships.sqlite
  (persons + household tables, person_id as the join key everywhere).
- Every upsert also mirrors a human-readable profile.json into
  relationships/<person_id>/ — "the directory IS the agent": you can
  read who the entity knows with `cat`, and the sqlite is rebuildable
  from the mirrors if it ever corrupts.
- The per-person ACL *declaration* ({scope, allowed_contexts}) is the
  person's standing privacy preference. It feeds WRITE-time defaults
  (what scope new memories about them get); READ-time enforcement is
  always the AccessContext → compile_acl path. Declarations express
  intent; contexts enforce it.
- Household membership is its own table, not a trust-tier value:
  household is a *scope boundary*, trust is a judgment. They correlate
  but must be independently editable.
- Injectable clock, per repo convention.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional

from .acl import DEFAULT_SCOPE, validate_scope

TRUST_TIERS = ("stranger", "acquaintance", "standard", "trusted", "inner")

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS persons (
    person_id        TEXT PRIMARY KEY,
    name             TEXT NOT NULL DEFAULT '',
    aliases          TEXT NOT NULL DEFAULT '[]',   -- JSON list
    channels         TEXT NOT NULL DEFAULT '{}',   -- JSON {channel: handle}
    notes            TEXT NOT NULL DEFAULT '',
    trust_tier       TEXT NOT NULL DEFAULT 'standard',
    default_scope    TEXT NOT NULL DEFAULT 'shared',
    allowed_contexts TEXT NOT NULL DEFAULT '[]',   -- JSON list of kinds
    created_ts       REAL NOT NULL,
    updated_ts       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS household (
    person_id TEXT PRIMARY KEY REFERENCES persons(person_id),
    added_ts  REAL NOT NULL
);
"""

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_dirname(person_id: str) -> str:
    """person_id → filesystem-safe directory name (no traversal)."""
    cleaned = _SAFE_ID.sub("_", person_id).strip("._") or "person"
    return cleaned


class RelationshipStore:
    """Per-person relationship records rooted at an entity directory."""

    def __init__(self, entity_root: str, *,
                 clock: Callable[[], float] = time.time):
        self.entity_root = os.path.abspath(entity_root)
        self.rel_dir = os.path.join(self.entity_root, "relationships")
        os.makedirs(self.rel_dir, exist_ok=True)
        self.clock = clock
        self.db_path = os.path.join(self.rel_dir, "relationships.sqlite")
        self.db = sqlite3.connect(self.db_path, check_same_thread=False)  # shell serializes cross-thread access (Phase 5)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self.db.commit()

    # ── lifecycle ─────────────────────────────────────────────────────
    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "RelationshipStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── persons ───────────────────────────────────────────────────────
    def upsert_person(
        self,
        person_id: str,
        *,
        name: Optional[str] = None,
        aliases: Optional[Iterable[str]] = None,
        channels: Optional[Dict[str, str]] = None,
        notes: Optional[str] = None,
        trust_tier: Optional[str] = None,
        acl: Optional[Dict[str, Any]] = None,   # {scope, allowed_contexts}
    ) -> Dict[str, Any]:
        """Create or update a person. Only provided fields change."""
        if not person_id or not str(person_id).strip():
            raise ValueError("person_id must be non-empty")
        person_id = str(person_id).strip()
        if trust_tier is not None and trust_tier not in TRUST_TIERS:
            raise ValueError(
                f"unknown trust tier {trust_tier!r}; known: {TRUST_TIERS}")
        acl = acl or {}
        if "scope" in acl:
            validate_scope(acl["scope"])

        now = self.clock()
        existing = self._row(person_id)
        if existing is None:
            self.db.execute(
                "INSERT INTO persons (person_id, name, aliases, channels,"
                " notes, trust_tier, default_scope, allowed_contexts,"
                " created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    person_id,
                    name or person_id,
                    json.dumps(list(aliases or []), ensure_ascii=False),
                    json.dumps(dict(channels or {}), ensure_ascii=False),
                    notes or "",
                    trust_tier or "standard",
                    acl.get("scope", DEFAULT_SCOPE),
                    json.dumps(list(acl.get("allowed_contexts") or []),
                               ensure_ascii=False),
                    now, now,
                ),
            )
        else:
            fields: List[str] = ["updated_ts=?"]
            params: List[Any] = [now]
            if name is not None:
                fields.append("name=?"); params.append(name)
            if aliases is not None:
                fields.append("aliases=?")
                params.append(json.dumps(list(aliases), ensure_ascii=False))
            if channels is not None:
                fields.append("channels=?")
                params.append(json.dumps(dict(channels), ensure_ascii=False))
            if notes is not None:
                fields.append("notes=?"); params.append(notes)
            if trust_tier is not None:
                fields.append("trust_tier=?"); params.append(trust_tier)
            if "scope" in acl:
                fields.append("default_scope=?"); params.append(acl["scope"])
            if "allowed_contexts" in acl:
                fields.append("allowed_contexts=?")
                params.append(json.dumps(list(acl["allowed_contexts"]),
                                         ensure_ascii=False))
            params.append(person_id)
            self.db.execute(
                f"UPDATE persons SET {', '.join(fields)} WHERE person_id=?",
                params)
        self.db.commit()
        person = self.get_person(person_id)
        assert person is not None
        self._mirror_profile(person)
        return person

    def _row(self, person_id: str) -> Optional[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM persons WHERE person_id=?", (person_id,)
        ).fetchone()

    def get_person(self, person_id: str) -> Optional[Dict[str, Any]]:
        row = self._row(person_id)
        return self._person_dict(row) if row else None

    def list_people(self) -> List[Dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM persons ORDER BY person_id").fetchall()
        return [self._person_dict(r) for r in rows]

    def resolve(self, handle: str) -> Optional[str]:
        """Resolve a name / alias / channel handle → person_id."""
        handle_l = str(handle).strip().lower()
        for p in self.list_people():
            if p["person_id"].lower() == handle_l:
                return p["person_id"]
            if p["name"].lower() == handle_l:
                return p["person_id"]
            if any(a.lower() == handle_l for a in p["aliases"]):
                return p["person_id"]
            if any(str(v).lower() == handle_l for v in p["channels"].values()):
                return p["person_id"]
        return None

    def _person_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        def _load(text: str, default: Any) -> Any:
            try:
                return json.loads(text)
            except (TypeError, ValueError):
                return default
        return {
            "person_id": row["person_id"],
            "name": row["name"],
            "aliases": _load(row["aliases"], []),
            "channels": _load(row["channels"], {}),
            "notes": row["notes"],
            "trust_tier": row["trust_tier"],
            "acl": {
                "scope": row["default_scope"],
                "allowed_contexts": _load(row["allowed_contexts"], []),
            },
            "household": self.is_household(row["person_id"]),
            "created_ts": row["created_ts"],
            "updated_ts": row["updated_ts"],
        }

    # ── per-person directory mirror ───────────────────────────────────
    def person_dir(self, person_id: str) -> str:
        return os.path.join(self.rel_dir, _safe_dirname(person_id))

    def _mirror_profile(self, person: Dict[str, Any]) -> None:
        d = self.person_dir(person["person_id"])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "profile.json"), "w", encoding="utf-8") as f:
            json.dump(person, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")

    # ── household ─────────────────────────────────────────────────────
    def add_to_household(self, person_id: str) -> None:
        if self._row(person_id) is None:
            raise KeyError(f"unknown person {person_id!r}; upsert first")
        self.db.execute(
            "INSERT OR IGNORE INTO household (person_id, added_ts)"
            " VALUES (?,?)", (person_id, self.clock()))
        self.db.commit()
        person = self.get_person(person_id)
        if person:
            self._mirror_profile(person)

    def remove_from_household(self, person_id: str) -> None:
        self.db.execute(
            "DELETE FROM household WHERE person_id=?", (person_id,))
        self.db.commit()
        person = self.get_person(person_id)
        if person:
            self._mirror_profile(person)

    def is_household(self, person_id: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM household WHERE person_id=?", (person_id,)
        ).fetchone() is not None

    def household_members(self) -> FrozenSet[str]:
        rows = self.db.execute("SELECT person_id FROM household").fetchall()
        return frozenset(r["person_id"] for r in rows)

    # ── write-time scope defaults from declarations ───────────────────
    def default_scope_for(self, person_id: str) -> tuple[str, Optional[str]]:
        """(scope, owner) default for memories about person_id, from
        their standing ACL declaration. Unknown person → shared."""
        person = self.get_person(person_id)
        if person is None:
            return (DEFAULT_SCOPE, None)
        scope = person["acl"].get("scope", DEFAULT_SCOPE)
        owner = person_id if scope == "private" else None
        return (scope, owner)

    # ── stats ─────────────────────────────────────────────────────────
    def stats(self) -> Dict[str, Any]:
        return {
            "people": int(self.db.execute(
                "SELECT COUNT(*) FROM persons").fetchone()[0]),
            "household": int(self.db.execute(
                "SELECT COUNT(*) FROM household").fetchone()[0]),
        }
