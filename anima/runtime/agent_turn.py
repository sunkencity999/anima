"""The act phase — wake → model turns → effects (PHASE5_RUNTIME.md).

Agent turn contract:

    wake ──► orient(wake)                             # Phase 2
          ──► prompt = identity ⊕ orient ⊕ wake payload
          ──► loop (bounded by budget):
                 routed = router.complete(tier(wake), messages,
                                          tools=registry.schemas(risk_cap))
                 tool_calls → execute via registry, append results, continue
                 else      → final
          ──► wake report ──► settle guard             # Phase 2

Key decisions (from the design note):
- The model drafts its own settle report: the final message may carry a
  ```settle fenced JSON block (or a bare JSON tail). If it doesn't, the
  runtime synthesizes the report from the ledger trail of the turn —
  the enforced-settle guarantee never depends on model discipline.
- Tier selection is wake-derived (message→standard, drive→reflex,
  sense→standard, timer→reflex), overridable via wake.payload["tier"]
  or a custom tier_map. Nobody hand-picks models per call; policy does.
- Every tool execution is a ledger row AND an episodic-eligible event
  (that happens inside ToolRegistry.execute).
- AccessContext discipline: message wakes carry their context in the
  payload (Phase 4). A non-message wake with no context is the entity
  waking ITSELF (drive/timer) and runs as a system context — the
  organism is allowed to know its own mind. A message wake missing a
  context falls back to direct-with-sender, never system.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional

from ..relationships import AccessContext
from ..relationships.acl import default_scope_for_context
from ..wake.orient import orient
from ..wake.sources import Wake
from .tools import BudgetExhausted, ToolRegistry, TurnContext, normalize_risk_cap

DEFAULT_TIER_MAP = {
    "message": "standard",
    "drive": "reflex",
    "sense": "standard",
    "timer": "reflex",
}

_SETTLE_FENCE_RE = re.compile(
    r"```settle[^\n]*\n(.*?)```", re.DOTALL | re.IGNORECASE)

_SETTLE_INSTRUCTIONS = """\
## How to act
Use the provided tools to act.

RULE — replying (non-negotiable): the ONLY way the person who triggered
this wake ever sees your words is the `reply` tool. Plain assistant text
is discarded unread. If this wake came from a message, you MUST call
`reply` with your answer BEFORE writing any final text. Never put your
answer in plain text — that is a silent failure.

Correct turn shape for a message wake:
1. (optional) other tool calls — recall, senses, etc.
2. `reply({"text": "...your answer to them..."})`  ← required
3. final plain message: settle block only, nothing else.

Wrong (your answer is lost): finishing with plain text and no `reply`
call.

When you are done, end your FINAL message with a settle block:

```settle
{"summary": "one-line honest account of what happened",
 "learnings": ["anything worth keeping long-term"],
 "decisions": ["decisions made, if any"],
 "drive_satisfactions": {"drive_name": 0.5}}
```

Every key is optional. Be honest — this becomes your memory."""


def tier_for_wake(wake: Wake,
                  tier_map: Optional[Dict[str, str]] = None) -> str:
    """Wake-derived routing tier. wake.payload['tier'] overrides."""
    override = (wake.payload or {}).get("tier")
    if isinstance(override, str) and override:
        return override
    mapping = dict(DEFAULT_TIER_MAP)
    if tier_map:
        mapping.update(tier_map)
    return mapping.get(wake.source, "standard")


def parse_settle_block(content: str) -> Optional[dict]:
    """Extract the model-drafted settle dict from a final message.

    Accepts a ```settle fenced block first; falls back to a bare JSON
    object at the tail of the message. Returns None when neither parses
    to a dict — the caller synthesizes from the ledger trail instead.
    """
    if not content:
        return None
    m = _SETTLE_FENCE_RE.search(content)
    if m:
        try:
            block = json.loads(m.group(1).strip())
            return block if isinstance(block, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None
    # JSON tail: the leftmost '{' whose suffix parses as a dict wins.
    stripped = content.rstrip()
    if not stripped.endswith("}"):
        return None
    for i, ch in enumerate(stripped):
        if ch != "{":
            continue
        try:
            block = json.loads(stripped[i:])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(block, dict):
            return block
    return None


def _context_for_wake(wake: Wake) -> Optional[AccessContext]:
    raw = (wake.payload or {}).get("access_context")
    if isinstance(raw, dict):
        return AccessContext.from_dict(raw)
    if wake.source == "message":
        sender = (wake.payload or {}).get("sender")
        if sender:
            return AccessContext.direct(str(sender), channel="chat")
        return None  # anonymous message: ACL-less would be wrong; recall
        # in this turn runs with context=None → single-user mode warning.
    # Self-initiated (drive/timer) or ambient sense wakes: the entity is
    # alone with its own mind.
    return AccessContext.system()


def _load_soul(entity: Any) -> str:
    path = os.path.join(entity.identity_dir, "soul.md")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""


def _user_message_for(wake: Wake) -> str:
    payload = wake.payload or {}
    if wake.source == "message":
        return f"[{payload.get('sender', 'unknown')}] {payload.get('text', '')}"
    body = {k: v for k, v in payload.items()
            if k not in ("access_context",) and isinstance(
                v, (str, int, float, bool))}
    return (f"Wake trigger — {wake.source}: {wake.reason}\n"
            + (json.dumps(body, ensure_ascii=False) if body else ""))


def _tool_call_parts(tc: dict) -> tuple[str, str, dict]:
    """(call_id, name, args) from an OpenAI-format tool call."""
    fn = tc.get("function") or {}
    name = str(fn.get("name") or tc.get("name") or "")
    raw_args = fn.get("arguments", tc.get("arguments"))
    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args)
        except (json.JSONDecodeError, ValueError):
            args = {}
    elif isinstance(raw_args, dict):
        args = raw_args
    else:
        args = {}
    if not isinstance(args, dict):
        args = {}
    return str(tc.get("id") or f"call-{name}"), name, args


def run_agent_turn(
    entity: Any,
    wake: Wake,
    registry: ToolRegistry,
    *,
    router: Any = None,
    tier_map: Optional[Dict[str, str]] = None,
    now: Optional[float] = None,
    max_iterations: Optional[int] = None,
) -> dict:
    """One full act phase. Returns the wake report dict for the settle
    guard. Router/transport failures propagate — the guard settles the
    failure episode (enforced settle, Phase 2)."""
    router = router if router is not None else entity.router
    if router is None:
        raise RuntimeError(
            "run_agent_turn requires a router (identity/routing.json or "
            "an injected Router)")
    now = now if now is not None else entity.clock()

    ctx = TurnContext(
        entity=entity,
        wake=wake,
        access_context=_context_for_wake(wake),
        now=now,
        actions_left=int((wake.budget or {}).get("max_actions", 20)),
        risk_cap=normalize_risk_cap((wake.budget or {}).get("risk_cap")),
        log_action=entity.ledger.bind(wake, clock=lambda: now),
    )

    soul = _load_soul(entity)
    orient_pack = orient(
        entity.store, wake, now=now,
        timer_source=entity.timers, drive_source=entity.drives,
        access_context=ctx.access_context,
        relationships=entity.relationships)
    system = "\n\n".join(p for p in (soul, orient_pack,
                                     _SETTLE_INSTRUCTIONS) if p)
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": _user_message_for(wake)},
    ]

    tier = tier_for_wake(wake, tier_map)
    tools = registry.schemas(ctx.risk_cap)
    hard_cap = (max_iterations if max_iterations is not None
                else ctx.actions_left + 3)

    final_content = ""
    model_used = ""
    budget_exhausted = False

    for _ in range(max(1, hard_cap)):
        routed = router.complete(tier, messages, tools=tools or None)
        model_used = routed.model_used
        ctx.log("model_call",
                f"tier={tier} tool_calls={len(routed.tool_calls or [])}",
                model=f"{routed.provider}/{routed.model_used}")

        if not routed.tool_calls:
            final_content = routed.content or ""
            # Reliability fix (2026-07-23): local models don't always call
            # the `reply` tool even when they produce a coherent answer
            # — the text lands in final_content and, without this fallback,
            # vanishes silently instead of reaching the sender. Treat any
            # non-empty final_content as an implicit reply when no reply
            # tool call was made during the turn. Strip any settle-block
            # trailer first so it doesn't leak into the surfaced message.
            if final_content and not ctx.replies:
                surface = final_content
                sb_idx = surface.find("<settle>")
                if sb_idx >= 0:
                    surface = surface[:sb_idx].rstrip()
                if surface:
                    ctx.replies.append(surface)
                    ctx.log("reply",
                            "implicit reply from final_content "
                            f"({len(surface)} chars)",
                            model=f"{routed.provider}/{routed.model_used}")
            break

        messages.append({
            "role": "assistant",
            "content": routed.content or None,
            "tool_calls": routed.tool_calls,
        })
        try:
            for tc in routed.tool_calls:
                call_id, name, args = _tool_call_parts(tc)
                result = registry.execute(name, args, ctx)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(result, ensure_ascii=False,
                                          default=str),
                })
        except BudgetExhausted as exc:
            budget_exhausted = True
            ctx.log("budget_exhausted", str(exc), outcome="error")
            break
    else:
        # Iteration hard-cap hit without a final: report truthfully.
        budget_exhausted = True
        ctx.log("budget_exhausted",
                "iteration cap hit without final message", outcome="error")

    # ── report assembly ───────────────────────────────────────────────
    d_scope, d_owner = default_scope_for_context(ctx.access_context,
                                                 ctx.sender)
    report: Dict[str, Any] = {
        "wake_id": wake.wake_id,
        "ts": now,
        "scope": d_scope,
        "owner": d_owner,
        "events": list(ctx.events),
        "decisions": list(ctx.decisions),
        "learnings": list(ctx.learnings),
        # metadata (ignored by settle(), kept for reply routing/audit):
        "replies": list(ctx.replies),
        "final": final_content,
        "tier": tier,
        "model": model_used,
        "actions_used": ctx.actions_used,
        "budget_exhausted": budget_exhausted,
    }
    if ctx.drive_satisfactions:
        report["drive_satisfactions"] = dict(ctx.drive_satisfactions)

    if budget_exhausted:
        report["events"].append({
            "summary": (f"budget exhausted: max_actions="
                        f"{(wake.budget or {}).get('max_actions')} spent "
                        f"after {ctx.actions_used} action(s); turn ended "
                        "with work possibly unfinished"),
            "kind": "event",
            "tags": ["budget-exhausted", wake.source],
        })
        return report

    settle_block = parse_settle_block(final_content)
    if settle_block:
        summary = settle_block.get("summary")
        if isinstance(summary, str) and summary.strip():
            report["events"].append({
                "summary": summary.strip()[:300],
                "detail": final_content[:1000],
                "kind": "event",
                "tags": ["settle", wake.source],
            })
        for key in ("learnings", "decisions", "events"):
            extra = settle_block.get(key)
            if isinstance(extra, list):
                report[key].extend(
                    x for x in extra if isinstance(x, (str, dict)))
        drives = settle_block.get("drive_satisfactions")
        if isinstance(drives, dict):
            merged = dict(report.get("drive_satisfactions") or {})
            merged.update({str(k): v for k, v in drives.items()
                           if isinstance(v, (int, float))})
            if merged:
                report["drive_satisfactions"] = merged
    else:
        # Synthesize from the ledger trail of the turn: ctx.events
        # already holds one event per tool execution; add the outcome.
        report["events"].append({
            "summary": (f"wake handled ({wake.source}: {wake.reason})"
                        + (f" — {final_content[:160]}" if final_content
                           else " — no final message")),
            "detail": final_content[:1000],
            "kind": "event",
            "tags": ["turn", wake.source],
        })
    return report


def attach_agent_turn(
    entity: Any,
    registry: ToolRegistry,
    *,
    router: Any = None,
    tier_map: Optional[Dict[str, str]] = None,
) -> Callable[[Wake], dict]:
    """Wire the act phase in as the entity's wake handler. Returns the
    handler (also installed on entity.scheduler / entity.handler)."""

    def handler(wake: Wake) -> dict:
        return run_agent_turn(
            entity, wake, registry,
            router=router, tier_map=tier_map, now=entity.clock())

    entity.handler = handler
    entity.scheduler.handler = handler
    return handler
