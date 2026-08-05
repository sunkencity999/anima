"""Orient phase (ARCHITECTURE.md §1 step 1).

Builds the working-memory context pack for a wake: what triggered it,
relevant episodic/semantic recall, open intentions (scheduled timers),
and drive pressure. Returned as markdown ready for prompt injection.

Design notes:
- The recall query is derived from the wake reason + salient payload
  strings (sender, text, sense kind, drive name) — the wake itself is
  the retrieval cue, per the architecture's "working memory rebuilt at
  wake" principle.
- Timer and drive sources are optional: a minimal runtime that only has
  messages still gets a valid pack.
- Phase 5 addition: orient accepts the wake's access_context (plus the
  RelationshipStore for household membership) and passes it into recall,
  so the PROMPT itself is ACL-walled — not just the recall tool. Without
  it, a group wake's orient pack could leak private rows straight into
  the model context, defeating the Phase 4 walls.
"""

from __future__ import annotations

from typing import Optional

from ..memory.graph import decide_recall_mode, recall_graph, render_graph_lines
from ..memory.recall import recall, recall_items
from ..memory.store import MemoryStore
from .sources import DriveSource, TimerSource, Wake


def _iso(ts: float, now: float) -> str:
    delta = ts - now
    if delta <= 0:
        return "due now"
    if delta < 3600:
        return f"in {delta / 60:.0f}m"
    if delta < 86400:
        return f"in {delta / 3600:.1f}h"
    return f"in {delta / 86400:.1f}d"


def derive_query(wake: Wake) -> str:
    """Turn a wake into a recall query string."""
    parts = [wake.reason]
    payload = wake.payload or {}
    for k in ("sender", "text", "kind", "drive", "topic", "subject"):
        v = payload.get(k)
        if isinstance(v, str) and v:
            parts.append(v)
    return " ".join(parts)[:400]


def orient(
    store: MemoryStore,
    wake: Wake,
    *,
    now: float,
    timer_source: Optional[TimerSource] = None,
    drive_source: Optional[DriveSource] = None,
    token_budget: int = 1500,
    max_intentions: int = 8,
    access_context: Optional[object] = None,
    relationships: Optional[object] = None,
) -> str:
    """Build the orient-phase markdown context pack for a wake."""
    lines: list[str] = [
        f"# Wake: {wake.wake_id}",
        "",
        "## Trigger",
        f"- source: **{wake.source}** (priority {wake.priority})",
        f"- reason: {wake.reason}",
    ]
    budget = wake.budget or {}
    if budget:
        lines.append(
            "- budget: "
            + ", ".join(f"{k}={v}" for k, v in sorted(budget.items())))
    coalesced = (wake.payload or {}).get("coalesced_count")
    if coalesced:
        lines.append(f"- coalesced: {coalesced} additional wake(s) merged in")
    payload_items = {
        k: v for k, v in (wake.payload or {}).items()
        if k not in ("coalesced",) and isinstance(v, (str, int, float, bool))
    }
    for k, v in sorted(payload_items.items()):
        lines.append(f"- {k}: {v}")
    lines.append("")

    # ── memory recall keyed on the wake itself ────────────────────────
    query = derive_query(wake)
    pack = recall(store, query, token_budget=token_budget,
                  now=now, access_context=access_context,
                  relationships=relationships)
    lines.append(pack.rstrip())
    lines.append("")

    # ── graph recall: the web behind the list (Phase 7) ───────────────
    # Flat is the default (cheap). When the wake looks multi-hop —
    # why/how-did-we-get-here/history cues, or flat results that are
    # all similarity and no diversity — the delegated retriever walks
    # the graph and hands back distilled nodes with their rel-chains.
    # The wake payload may force a mode ("recall_mode": "graph"|"flat").
    mode = (wake.payload or {}).get("recall_mode")
    if mode not in ("graph", "flat"):
        try:
            flat = recall_items(
                store, query, max_items=6, now=now,
                access_context=access_context,
                relationships=relationships)
            mode = decide_recall_mode(query, flat["episodes"])
        except Exception:
            mode = "flat"   # a broken heuristic must not block orient
    if mode == "graph":
        try:
            walked = recall_graph(
                store, query, now=now,
                access_context=access_context,
                relationships=relationships)
            connected = [w for w in walked if w["rel_chain"]]
            if connected:
                lines.append("## Graph recall (connected memories)")
                lines.extend(render_graph_lines(
                    [w for w in walked if not w["rel_chain"]][:4]
                    + connected))
                lines.append("")
        except Exception:
            pass   # graph recall is an organ, not a dependency

    # ── open intentions ───────────────────────────────────────────────
    if timer_source is not None:
        intentions = timer_source.open_intentions(now)[:max_intentions]
        if intentions:
            lines.append("## Open intentions")
            for it in intentions:
                recur = (f" (every {it['interval'] / 3600:.1f}h)"
                         if it["kind"] == "every" and it["interval"] else "")
                lines.append(
                    f"- [{_iso(it['next_ts'], now)}] {it['reason']}{recur}")
            lines.append("")

    # ── drive pressure ────────────────────────────────────────────────
    if drive_source is not None:
        summary = drive_source.pressure_summary(now)
        if summary:
            lines.append("## Drive pressure")
            for d in sorted(summary, key=lambda x: -x["fraction"]):
                bar = "█" * min(10, int(round(d["fraction"] * 10)))
                latch = " (wake pending)" if d["pending"] else ""
                lines.append(
                    f"- {d['name']}: {d['pressure']:.2f}/{d['threshold']:.2f} "
                    f"`{bar:<10}`{latch}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
