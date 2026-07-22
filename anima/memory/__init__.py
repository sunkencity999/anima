"""ANIMA memory engine — episodic / semantic / procedural substrate.

Phase 1 keystone (ARCHITECTURE.md §2, Build Order #1).

Public surface:
    MemoryStore      — SQLite+FTS5 backed three-layer store (store.py)
    recall           — hybrid keyword+recency+entity recall → markdown pack (recall.py)
    settle           — settle-phase writer for wake reports (settle.py)
    consolidate      — consolidation pass, LLM-backed or heuristic dry-run (consolidate.py)
"""

from .store import MemoryStore
from .recall import recall
from .settle import settle
from .consolidate import run_consolidation

__all__ = ["MemoryStore", "recall", "settle", "run_consolidation"]
