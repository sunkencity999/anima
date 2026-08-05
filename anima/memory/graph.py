"""Graph recall — memory as a web, not a list (Phase 7).

Flat retrieval answers "what is *similar* to this query." The graph
answers "what is *connected* to it": decision → the constraint that
drove it → the incident that created the constraint. This module is
the retriever half of the pattern — traversal is delegated here so it
burns the retriever's budget, not the thinker's; the wake that needs
the memory receives only distilled nodes with their rel-chains.

Pieces:
- seed_nodes()        — flat recall hits, lazily node-ified
- recall_graph()      — ACL-respecting 1–2 hop walk → ≤12 scored nodes
- decide_recall_mode()— flat (default, cheap) vs graph (multi-hop cues)
- render_graph_lines()— compact context lines, rel-chain included
- graph_gc()          — rot resistance: prune, merge stubs, count
                        orphans (the part prototypes skip)

Design decisions:
- Traversal itself is deterministic (pure sqlite walk + scoring); the
  model's contribution to the graph happens at settle-time extraction
  (graph_extract.py). A deterministic retriever cannot hallucinate an
  edge that isn't there.
- ACL is enforced at every traversed node INSIDE sqlite (see
  MemoryStore.neighbors): a private node reached from a public seed is
  still private — the Phase 5 lesson, applied to traversal.
"""

from __future__ import annotations

import math
import re
import time
from typing import Any, Iterable, Optional

from .recall import recall_items
from .store import MemoryStore

LN2 = math.log(2)

# Episode kind → node kind. Anything unrecognized is plain "memory".
_EPISODE_NODE_KIND = {
    "decision": "decision",
    "learning": "belief",
    "event": "event",
    "message": "event",
    "commitment": "commitment",
}

# Multi-hop cues: why / how-did-we-get-here / history questions want
# the web, not the list.
_GRAPH_CUE = re.compile(
    r"\b(why|how did|how come|history|because|what led|led to|origin|"
    r"caused|reason|backstory|how'd)\b", re.IGNORECASE)


def _node_kind_for_episode(kind: str) -> str:
    return _EPISODE_NODE_KIND.get(kind, "memory")


def ensure_episode_node(store: MemoryStore, ep: dict) -> int:
    """Lazy node-ification of an episodic row."""
    return store.node_for_memory(
        "episodic", int(ep["id"]),
        kind=_node_kind_for_episode(str(ep.get("kind", "event"))),
        label=str(ep.get("summary", ""))[:200] or "(untitled episode)",
        body=str(ep.get("detail", "") or ep.get("summary", "")),
        ts=ep.get("ts"),
        scope=str(ep.get("scope", "shared")),
        owner=ep.get("owner_person_id"))


def ensure_belief_node(store: MemoryStore, belief: dict) -> int:
    """Lazy node-ification of a semantic row."""
    return store.node_for_memory(
        "semantic", int(belief["id"]),
        kind="belief",
        label=str(belief.get("statement", ""))[:200] or "(empty belief)",
        body=str(belief.get("statement", "")),
        ts=belief.get("created_ts"),
        scope=str(belief.get("scope", "shared")),
        owner=belief.get("owner_person_id"))


def seed_nodes(
    store: MemoryStore,
    query: str,
    *,
    now: Optional[float] = None,
    access_context: Optional[Any] = None,
    relationships: Optional[Any] = None,
    max_seeds: int = 6,
    create: bool = True,
) -> list[int]:
    """Flat recall → node ids. The existing search IS the seed source
    (spec non-goal: no embedding changes). create=False is the
    read-only path (HTTP-thread marginalia): only memories that
    already HAVE nodes seed the walk — no writes off the wake path."""
    items = recall_items(
        store, query, max_items=max_seeds, now=now,
        access_context=access_context, relationships=relationships)
    seeds: list[int] = []
    for ep in items["episodes"][:max_seeds]:
        if create:
            seeds.append(ensure_episode_node(store, ep))
        else:
            nid = store.get_node_id_for_memory("episodic", int(ep["id"]))
            if nid is not None:
                seeds.append(nid)
    for b in items["beliefs"][:max(0, max_seeds - len(seeds))]:
        if create:
            seeds.append(ensure_belief_node(store, b))
        else:
            nid = store.get_node_id_for_memory("semantic", int(b["id"]))
            if nid is not None:
                seeds.append(nid)
    return seeds


def _recency(ts: float, now: float, half_life_days: float = 30.0) -> float:
    age_days = max(0.0, (now - (ts or now)) / 86400.0)
    return math.exp(-age_days / half_life_days * LN2)


def _touch_boost(touch_count: int) -> float:
    return 1.0 + 0.1 * math.log1p(max(0, touch_count))


def recall_graph(
    store: MemoryStore,
    query: str,
    *,
    access_context: Optional[Any] = None,
    relationships: Optional[Any] = None,
    hop_budget: int = 2,
    node_budget: int = 12,
    now: Optional[float] = None,
    touch: bool = True,
    readonly: bool = False,
) -> list[dict]:
    """The delegated retriever: vector/text seeds → bounded ACL-walled
    walk → ≤ node_budget compact nodes, each carrying the rel-chain
    that explains WHY it arrived.

    readonly=True (implies touch=False) is for HTTP-thread callers
    (Observatory marginalia): single-writer discipline means no store
    writes off the wake path, so seeds are limited to memories already
    node-ified and nothing is touched.

    Returns [{node_id, kind, label, snippet, rel_chain, score,
    hops}], best-first. Seeds come first (hops=0, empty chain)."""
    now = now if now is not None else time.time()
    if readonly:
        touch = False

    acl = None
    if access_context is not None:
        from ..relationships.acl import compile_acl
        household = (relationships.household_members()
                     if relationships is not None else frozenset())
        acl = compile_acl(access_context, household)

    seeds = seed_nodes(store, query, now=now,
                       access_context=access_context,
                       relationships=relationships,
                       create=not readonly)
    if not seeds:
        return []

    # node_id -> best (score, hops, chain [(rel, direction, label), ...])
    best: dict[int, tuple[float, int, list]] = {}
    for nid in seeds:
        node = store.get_node(nid)
        if node is None:
            continue
        score = (node["weight"] * _recency(node["last_touched"], now)
                 * _touch_boost(node["touch_count"]))
        best[nid] = (max(score, 0.05), 0, [])

    frontier = set(best)
    visited = set(best)
    for hop in range(1, max(0, hop_budget) + 1):
        if not frontier:
            break
        found = store.neighbors(frontier, acl=acl)
        next_frontier: set[int] = set()
        for hit in found:
            node = hit["node"]
            nid = node["id"]
            parent_score, _, parent_chain = best.get(
                hit["from"], (0.05, hop - 1, []))
            arrow = ("→" if hit["direction"] == "out" else "←")
            chain = parent_chain + [f"{arrow}{hit['rel']}"]
            score = (parent_score
                     * hit["edge_weight"]
                     * node["weight"]
                     * _recency(node["last_touched"], now)
                     * _touch_boost(node["touch_count"]))
            prev = best.get(nid)
            if prev is None or score > prev[0]:
                best[nid] = (score, hop, chain)
            if nid not in visited:
                visited.add(nid)
                next_frontier.add(nid)
        frontier = next_frontier

    ranked = sorted(best.items(),
                    key=lambda kv: (kv[1][1] == 0, kv[1][0]),
                    reverse=True)[:max(1, node_budget)]
    out = []
    for nid, (score, hops, chain) in ranked:
        node = store.get_node(nid)
        if node is None:
            continue
        snippet = (node["body"] or node["label"])[:240]
        out.append({
            "node_id": nid,
            "kind": node["kind"],
            "label": node["label"],
            "snippet": snippet,
            "rel_chain": list(chain),
            "score": round(score, 4),
            "hops": hops,
            "stub": node["stub"],
        })
    if touch and out:
        store.touch_nodes([o["node_id"] for o in out], ts=now)
    return out


def decide_recall_mode(query: str, flat_episodes: Iterable[dict]) -> str:
    """flat (default, cheap) or graph (the wake looks multi-hop).

    Graph when the query carries why/how/history cues, or when flat
    recall returned high-similarity-low-diversity results (several
    episodes that all say nearly the same thing — similarity is
    saturated, connection is what's missing)."""
    if _GRAPH_CUE.search(query or ""):
        return "graph"
    eps = list(flat_episodes)
    if len(eps) >= 4:
        sets = [set(re.findall(r"[a-z0-9]+",
                               str(e.get("summary", "")).lower()))
                for e in eps[:6]]
        sims = []
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                a, b = sets[i], sets[j]
                if a and b:
                    sims.append(len(a & b) / len(a | b))
        if sims and sum(sims) / len(sims) > 0.6:
            return "graph"
    return "flat"


def render_graph_lines(items: Iterable[dict]) -> list[str]:
    """`[kind] label — snippet (rel-chain)` — the rel-chain is the
    point: the thinker sees WHY each node arrived."""
    lines = []
    for it in items:
        chain = (" (" + " ".join(it["rel_chain"]) + ")"
                 if it.get("rel_chain") else " (seed)")
        body = it.get("snippet") or ""
        sep = f" — {body}" if body and body != it["label"] else ""
        lines.append(f"- [{it['kind']}] {it['label']}{sep}{chain}")
    return lines


# ── rot resistance (spec §5) ─────────────────────────────────────────

def graph_gc(
    store: MemoryStore,
    *,
    now: Optional[float] = None,
    prune_threshold: float = 0.05,
    half_life_days: float = 90.0,
) -> dict:
    """Prune edges whose weight × age-decay fell below threshold, merge
    duplicate stub nodes, report orphan counts. The graph is allowed to
    forget its weakest connections — that is maintenance, not loss;
    the underlying memories are untouched."""
    now = now if now is not None else time.time()
    db = store.db

    # 1. prune decayed edges
    pruned = 0
    for row in db.execute(
            "SELECT id, weight, created_at FROM edges").fetchall():
        effective = row["weight"] * _recency(
            row["created_at"], now, half_life_days)
        if effective < prune_threshold:
            db.execute("DELETE FROM edges WHERE id=?", (row["id"],))
            pruned += 1

    # 2. merge duplicate stubs (same kind + case-insensitive label):
    #    keep the oldest, repoint edges, drop the rest.
    merged = 0
    dupes = db.execute(
        "SELECT kind, label, COUNT(*) AS n, MIN(id) AS keeper"
        " FROM nodes WHERE stub=1"
        " GROUP BY kind, label COLLATE NOCASE HAVING n > 1").fetchall()
    for d in dupes:
        keeper = int(d["keeper"])
        extras = [int(r["id"]) for r in db.execute(
            "SELECT id FROM nodes WHERE stub=1 AND kind=? AND"
            " label=? COLLATE NOCASE AND id != ?",
            (d["kind"], d["label"], keeper)).fetchall()]
        for ex in extras:
            db.execute("UPDATE OR IGNORE edges SET src=? WHERE src=?",
                       (keeper, ex))
            db.execute("UPDATE OR IGNORE edges SET dst=? WHERE dst=?",
                       (keeper, ex))
            # a repoint that collides with an existing (src,dst,rel)
            # is a duplicate edge: drop it.
            db.execute("DELETE FROM edges WHERE src=? OR dst=?",
                       (ex, ex))
            db.execute("DELETE FROM nodes WHERE id=?", (ex,))
            merged += 1
    # self-loops can appear after merges; they explain nothing.
    db.execute("DELETE FROM edges WHERE src = dst")
    db.commit()

    stats = store.graph_stats()
    return {
        "pruned_edges": pruned,
        "merged_stubs": merged,
        **stats,
    }
