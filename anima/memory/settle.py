"""Settle-phase writer (ARCHITECTURE.md §1, step 3).

Takes a structured "wake report" dict and durably records the wake:
episodic events for everything that happened, plus consolidation-queue
candidates for anything learned. Settlement is meant to be *enforced by
the runtime* — this module is the primitive the runtime calls.

Wake report shape (all keys optional except nothing — an empty report is
a valid no-op wake):

    {
      "wake_id": "wake-2026-07-22-0850",     # generated if absent
      "ts": 1784000000.0,                     # defaults to now
      "events": [                             # what happened
        {"summary": "...", "detail": "...", "kind": "event",
         "actors": ["Christopher"], "tags": ["anima"]},
        "bare strings are accepted too",
      ],
      "decisions": ["...", {"summary": "...", ...}],   # kind=decision
      "learnings": ["...", {"summary": "...", ...}],   # kind=learning
                                              # → also queued for consolidation
      "drive_satisfactions": {"craft": 0.8, "curiosity": 0.3},
    }

Returns a receipt dict: wake_id, episode_ids, queued candidate ids.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional, Union

from .store import MemoryStore


def _normalize(item: Union[str, dict], default_kind: str) -> dict:
    if isinstance(item, str):
        return {"summary": item, "detail": "", "kind": default_kind,
                "actors": [], "tags": []}
    out = dict(item)
    out.setdefault("summary", out.get("detail", "")[:120] or "(no summary)")
    out.setdefault("detail", "")
    out.setdefault("kind", default_kind)
    out.setdefault("actors", [])
    out.setdefault("tags", [])
    return out


def settle(store: MemoryStore, wake_report: dict[str, Any]) -> dict:
    """Write a wake report into memory. Returns a settlement receipt."""
    if not isinstance(wake_report, dict):
        raise TypeError("wake_report must be a dict")

    wake_id = wake_report.get("wake_id") or f"wake-{uuid.uuid4().hex[:12]}"
    ts: Optional[float] = wake_report.get("ts")
    base_ts = ts if ts is not None else time.time()

    episode_ids: list[int] = []
    queued_ids: list[int] = []

    def write(item: Union[str, dict], default_kind: str) -> int:
        ev = _normalize(item, default_kind)
        eid = store.add_episode(
            summary=ev["summary"], detail=ev["detail"], kind=ev["kind"],
            actors=ev["actors"], tags=ev["tags"], wake_id=wake_id,
            ts=ev.get("ts", base_ts),
        )
        episode_ids.append(eid)
        return eid

    for item in wake_report.get("events", []) or []:
        write(item, "event")

    for item in wake_report.get("decisions", []) or []:
        write(item, "decision")

    for item in wake_report.get("learnings", []) or []:
        ev = _normalize(item, "learning")
        eid = write(ev, "learning")
        # Learnings are semantic candidates: queue for consolidation with
        # provenance pointing at the episode we just wrote.
        candidate_text = ev["detail"] or ev["summary"]
        qid = store.queue_candidate(
            candidate_text, episode_ids=[eid], hint="learning",
            wake_id=wake_id, ts=base_ts,
        )
        queued_ids.append(qid)

    drives = wake_report.get("drive_satisfactions") or {}
    if drives:
        summary = ", ".join(f"{k}={v:+.2f}" if isinstance(v, (int, float)) else f"{k}={v}"
                            for k, v in sorted(drives.items()))
        eid = store.add_episode(
            summary=f"drive satisfactions: {summary}",
            kind="drive", tags=["drive"] + sorted(drives.keys()),
            wake_id=wake_id, ts=base_ts,
        )
        episode_ids.append(eid)

    return {
        "wake_id": wake_id,
        "episode_ids": episode_ids,
        "queued_candidate_ids": queued_ids,
    }
