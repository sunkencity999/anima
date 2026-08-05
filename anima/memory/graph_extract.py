"""Settle-time edge extraction (Phase 7 §2).

After each wake settles, the entity is already summarizing what just
mattered — that is the natural moment to ask a reflex-tier model which
of those things are CONNECTED. The model emits edge candidates as JSON
(`[{src_hint, dst_hint, rel, confidence}]`); hints resolve against node
labels (exact → fuzzy); unresolvable hints become *stub nodes* — stubs
are how the graph discovers what it doesn't know yet.

Rules (spec, verbatim intent):
- confidence < 0.6 → dropped. Noisy edges are worse than no edges.
- extraction failure is non-fatal and logged; the organism never dies
  of bad bookkeeping.
- `supersedes` demotes the superseded node's weight, never deletes it.
  History stays walkable.
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Any, Optional

from .graph import ensure_episode_node
from .store import EDGE_RELS, MemoryStore

MIN_CONFIDENCE = 0.6
_FUZZY_CUTOFF = 0.72

# Unresolvable hint → stub node kind, derived from the rel that wanted
# it (spec: "kind from rel").
_STUB_KIND_FOR_REL = {
    "involves": "person",
    "caused": "event",
    "supersedes": "decision",
    "contradicts": "belief",
    "part_of": "artifact",
    "felt_about": "belief",
}

_SYSTEM_PROMPT = """\
You extract relationship edges from an agent's wake summary.
Given the settle report and the memories just written, list the
connections between the things mentioned.

Reply with ONLY a JSON array (no prose):
[{"src_hint": "<label or short phrase>",
  "dst_hint": "<label or short phrase>",
  "rel": "involves|caused|supersedes|contradicts|part_of|felt_about",
  "confidence": 0.0-1.0}]

Guidance:
- "caused": src led to / produced / explains dst.
- "supersedes": src replaces dst (a newer decision over an older one).
- "involves": src concerns the person/thing dst.
- Only edges you are confident about. An empty array [] is a fine
  answer. Never invent entities that were not mentioned."""

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def parse_edge_candidates(content: str) -> list[dict]:
    """Model output → validated candidate list. Tolerant of fenced
    blocks and prose around the array; intolerant of bad entries —
    anything malformed, off-whitelist, or under-confident is dropped
    (noisy edges are worse than no edges)."""
    if not content:
        return []
    m = _JSON_ARRAY_RE.search(content)
    if not m:
        return []
    try:
        raw = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        src = str(item.get("src_hint") or "").strip()
        dst = str(item.get("dst_hint") or "").strip()
        rel = str(item.get("rel") or "").strip()
        try:
            conf = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        if not src or not dst or src.lower() == dst.lower():
            continue
        if rel not in EDGE_RELS:
            continue
        if conf < MIN_CONFIDENCE:
            continue
        out.append({"src_hint": src[:200], "dst_hint": dst[:200],
                    "rel": rel, "confidence": min(1.0, conf)})
    return out


def resolve_hint(
    store: MemoryStore,
    hint: str,
    rel: str,
    *,
    create_stub: bool = True,
    ts: Optional[float] = None,
    scope: str = "shared",
    owner: Optional[str] = None,
) -> Optional[int]:
    """Hint → node id. Exact label match first, then fuzzy against the
    recent label pool, else a stub node (kind from rel)."""
    node = store.find_node_by_label(hint)
    if node is not None:
        return node["id"]
    pool = store.node_labels(limit=500)
    labels = [lbl for _, lbl in pool]
    close = difflib.get_close_matches(hint, labels, n=1,
                                      cutoff=_FUZZY_CUTOFF)
    if close:
        for nid, lbl in pool:
            if lbl == close[0]:
                return nid
    if not create_stub:
        return None
    return store.add_node(
        _STUB_KIND_FOR_REL.get(rel, "memory"), hint, "",
        stub=True, ts=ts, scope=scope, owner=owner)


def extract_edges_for_wake(
    store: MemoryStore,
    router: Any,
    report: dict,
    receipt: dict,
    *,
    now: Optional[float] = None,
    ledger: Any = None,
) -> dict:
    """The settle-time extraction pass. One bounded reflex-tier call;
    every failure path returns a summary dict instead of raising — the
    organism never dies of bad bookkeeping."""
    wake_id = str(report.get("wake_id") or receipt.get("wake_id") or "")
    summary = {"ok": True, "wake_id": wake_id, "edges_added": 0,
               "stubs_created": 0, "dropped": 0, "error": None}

    def log(kind: str, detail: str, outcome: str = "ok") -> None:
        if ledger is not None:
            try:
                ledger.log(wake_id, kind, detail, source="graph",
                           outcome=outcome)
            except Exception:
                pass

    try:
        # Node-ify the wake's new memory writes FIRST so hints can
        # resolve against them.
        d_scope = str(report.get("scope", "shared"))
        d_owner = report.get("owner")
        new_labels = []
        for eid in receipt.get("episode_ids") or []:
            ep = store.get_episode(int(eid))
            if ep is None:
                continue
            ensure_episode_node(store, ep)
            new_labels.append(str(ep["summary"])[:200])
        if not new_labels:
            return summary   # nothing settled, nothing to connect

        material = {
            "wake_summary": [
                str(e.get("summary", e) if isinstance(e, dict) else e)
                for e in (report.get("events") or [])][:8],
            "decisions": [str(d.get("summary", d)
                              if isinstance(d, dict) else d)
                          for d in (report.get("decisions") or [])][:8],
            "learnings": [str(l.get("summary", l)
                              if isinstance(l, dict) else l)
                          for l in (report.get("learnings") or [])][:8],
            "new_memories": new_labels[:10],
            "existing_labels": [lbl for _, lbl
                                in store.node_labels(limit=40)],
        }
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",
             "content": json.dumps(material, ensure_ascii=False,
                                   indent=1)},
        ]
        routed = router.complete("reflex", messages)
        candidates = parse_edge_candidates(routed.content or "")

        stub_count = {"n": 0}

        def resolve(hint: str, rel: str) -> Optional[int]:
            before = store.graph_stats()["stubs"]
            nid = resolve_hint(store, hint, rel, ts=now,
                               scope=d_scope, owner=d_owner)
            if store.graph_stats()["stubs"] > before:
                stub_count["n"] += 1
            return nid

        for cand in candidates:
            src = resolve(cand["src_hint"], cand["rel"])
            dst = resolve(cand["dst_hint"], cand["rel"])
            if src is None or dst is None or src == dst:
                summary["dropped"] += 1
                continue
            edge_id = store.add_edge(
                src, dst, cand["rel"], weight=cand["confidence"],
                evidence_wake_id=wake_id, ts=now)
            if edge_id is None:
                summary["dropped"] += 1
                continue
            summary["edges_added"] += 1
            if cand["rel"] == "supersedes":
                # demoted, never deleted — history stays walkable
                store.demote_node(dst, factor=0.5)
        summary["stubs_created"] = stub_count["n"]
        if summary["edges_added"] or summary["stubs_created"]:
            log("graph_extract",
                f"edges={summary['edges_added']} "
                f"stubs={summary['stubs_created']} "
                f"dropped={summary['dropped']}")
        return summary
    except Exception as exc:   # noqa: BLE001 — non-fatal by design
        summary["ok"] = False
        summary["error"] = f"{type(exc).__name__}: {exc}"
        log("graph_extract_failed", summary["error"], outcome="error")
        return summary
