"""Hybrid recall: FTS5 keyword relevance × temporal recency × entity/tag
filters, returning a ranked, token-budgeted markdown context pack.

Ranking model (design decision):
    combined = keyword_weight * norm_bm25 + recency_weight * recency
where
    norm_bm25  — bm25 scores mapped to (0,1] via 1/(1+max(0,score))
                 (sqlite FTS5 bm25 is "lower is better", usually negative
                 for strong matches, so strong matches land near 1.0)
    recency    — exp(-age_days / half_life_days * ln2), i.e. a true
                 half-life decay: an episode half_life_days old scores 0.5.

Token budgeting is approximate (len(text) // 4 ≈ tokens), which is the
standard cheap heuristic; the pack is trimmed to fit before rendering.
"""

from __future__ import annotations

import math
import time
import warnings
from typing import Any, Iterable, Optional

from .store import MemoryStore

LN2 = math.log(2)

# Emitted once per process when recall runs without an AccessContext.
_ACLLESS_WARNED = False


def _warn_aclless() -> None:
    global _ACLLESS_WARNED
    if not _ACLLESS_WARNED:
        _ACLLESS_WARNED = True
        warnings.warn(
            "ACL-less recall — single-user mode. Pass access_context to "
            "enforce relationship privacy walls (ARCHITECTURE.md §5).",
            UserWarning,
            stacklevel=3,
        )


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _norm_bm25(score: float) -> float:
    # FTS5 bm25: lower is better (negative for good matches).
    return 1.0 / (1.0 + max(0.0, score + 10.0) / 10.0) if score < 0 else 1.0 / (1.0 + score + 1.0)


def _recency(ts: float, now: float, half_life_days: float) -> float:
    age_days = max(0.0, (now - ts) / 86400.0)
    return math.exp(-age_days / half_life_days * LN2)


def _iso(ts: Optional[float]) -> str:
    if not ts:
        return "never"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _matches_filters(item: dict, actors: Optional[set], tags: Optional[set]) -> bool:
    if actors and not (actors & {a.lower() for a in item.get("actors", [])}):
        return False
    if tags and not (tags & {t.lower() for t in item.get("tags", [])}):
        return False
    return True


def recall_items(
    store: MemoryStore,
    query: str,
    *,
    actors: Optional[Iterable[str]] = None,
    tags: Optional[Iterable[str]] = None,
    max_items: int = 12,
    half_life_days: float = 14.0,
    keyword_weight: float = 0.7,
    recency_weight: float = 0.3,
    now: Optional[float] = None,
    access_context: Optional[Any] = None,
    relationships: Optional[Any] = None,
) -> dict:
    """Structured recall: the SELECTION half of recall(), returned as
    plain dicts instead of rendered markdown.

    Same ranking model, same ACL wall (compiled inside sqlite when an
    access_context is given). Used by recall() itself and by anything
    that needs to know *which* memories surfaced for a cue — e.g. the
    Observatory's conversation marginalia (Phase 6b v3).

    Returns {"episodes": [...], "beliefs": [...], "skills": [...]}.
    """
    now = now if now is not None else time.time()
    actor_set = {a.lower() for a in actors} if actors else None
    tag_set = {t.lower() for t in tags} if tags else None

    acl = None
    if access_context is not None:
        # Imported lazily so anima.memory stays importable standalone.
        from ..relationships.acl import compile_acl
        household = (relationships.household_members()
                     if relationships is not None else frozenset())
        acl = compile_acl(access_context, household)
    else:
        _warn_aclless()

    # ── episodes: hybrid rank ─────────────────────────────────────────
    candidates = store.search_episodes(query, limit=max_items * 4, acl=acl)
    if not candidates and (actor_set or tag_set):
        # No keyword hits: fall back to recent episodes matching filters.
        candidates = [dict(e, score=0.0)
                      for e in store.recent_episodes(max_items * 4, acl=acl)]

    ranked = []
    for ep in candidates:
        if not _matches_filters(ep, actor_set, tag_set):
            continue
        kw = _norm_bm25(ep.get("score", 0.0))
        rec = _recency(ep["ts"], now, half_life_days)
        ranked.append((keyword_weight * kw + recency_weight * rec, ep))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    episodes = [ep for _, ep in ranked[:max_items]]

    # ── beliefs: keyword match, active/stale only ─────────────────────
    beliefs = [
        b for b in store.search_beliefs(query, limit=max_items, acl=acl)
        if b["status"] != "contradicted" and _matches_filters(b, None, tag_set)
    ]

    # ── skills: name/description keyword overlap ──────────────────────
    q_tokens = {t.lower() for t in query.split()}
    skills = [
        s for s in store.list_skills(acl=acl)
        if q_tokens & {w.lower() for w in (s["name"].replace("-", " ").replace("_", " ")
                                           + " " + s["description"]).split()}
    ][:5]

    return {"episodes": episodes, "beliefs": beliefs, "skills": skills}


def recall(
    store: MemoryStore,
    query: str,
    *,
    actors: Optional[Iterable[str]] = None,
    tags: Optional[Iterable[str]] = None,
    token_budget: int = 1500,
    max_items: int = 12,
    half_life_days: float = 14.0,
    keyword_weight: float = 0.7,
    recency_weight: float = 0.3,
    now: Optional[float] = None,
    access_context: Optional[Any] = None,
    relationships: Optional[Any] = None,
) -> str:
    """Return a markdown context pack for prompt injection.

    When access_context (an anima.relationships.AccessContext) is given,
    the compiled ACL WHERE clause is applied inside every sqlite query
    (episodic, semantic, procedural) — rows outside the context's
    visibility never leave the database. `relationships` (a
    RelationshipStore) supplies household membership; without it the
    household set is empty and household-scoped rows stay hidden.

    When access_context is None, behavior is exactly pre-Phase-4
    (single-user mode) and a UserWarning is emitted once per process.
    """
    now = now if now is not None else time.time()
    items = recall_items(
        store, query, actors=actors, tags=tags, max_items=max_items,
        half_life_days=half_life_days, keyword_weight=keyword_weight,
        recency_weight=recency_weight, now=now,
        access_context=access_context, relationships=relationships)
    episodes = items["episodes"]
    beliefs = items["beliefs"]
    skills = items["skills"]

    # ── render within budget ──────────────────────────────────────────
    lines: list[str] = [f"## Recall: {query}", ""]
    used = _approx_tokens("\n".join(lines))

    def try_add(block: list[str]) -> bool:
        nonlocal used
        cost = _approx_tokens("\n".join(block))
        if used + cost > token_budget:
            return False
        lines.extend(block)
        used += cost
        return True

    if beliefs:
        try_add(["### Beliefs", ""])
        for b in beliefs:
            flag = " ⚠ stale — reverify" if b["status"] == "stale" else ""
            block = [f"- ({b['confidence']:.2f}){flag} {b['statement']} "
                     f"_(confirmed {_iso(b['last_confirmed'])}, "
                     f"episodes {b['provenance'] or '—'})_"]
            if not try_add(block):
                break
        try_add([""])

    if episodes:
        try_add(["### Episodes", ""])
        for ep in episodes:
            who = f" [{', '.join(ep['actors'])}]" if ep["actors"] else ""
            tag_str = f" `{' '.join('#' + t for t in ep['tags'])}`" if ep["tags"] else ""
            block = [f"- **{_iso(ep['ts'])}** ({ep['kind']}){who} {ep['summary']}{tag_str}"]
            if ep["detail"]:
                block.append(f"  - {ep['detail'][:400]}")
            if not try_add(block):
                break
        try_add([""])

    if skills:
        try_add(["### Relevant skills", ""])
        for s in skills:
            rate = f"{s['success_rate']:.0%}" if s["success_rate"] is not None else "untried"
            block = [f"- **{s['name']}** ({rate}, last worked {_iso(s['last_worked'])}): "
                     f"{s['description']}"]
            if s["known_failure_modes"]:
                block.append(f"  - known failures: {'; '.join(s['known_failure_modes'])}")
            if not try_add(block):
                break

    if len(lines) <= 2:
        lines.append("_No relevant memories._")
    return "\n".join(lines).rstrip() + "\n"


# Canonical Phase-4 name: the recall result IS a context pack, and the
# access_context parameter is where the privacy walls attach.
build_context_pack = recall
