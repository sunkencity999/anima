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
"""

from __future__ import annotations

from typing import Optional

from ..memory.recall import recall
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
    pack = recall(store, derive_query(wake), token_budget=token_budget, now=now)
    lines.append(pack.rstrip())
    lines.append("")

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
