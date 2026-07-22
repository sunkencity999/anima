"""Risk-tiered tool registry — the entity's hands (PHASE5_RUNTIME.md).

The wall is structural, the model is untrusted (same principle as the
Phase 4 ACLs). Two independent layers enforce the wake budget's
risk_cap:

  1. `schemas(risk_cap)` — the schema list OFFERED to the model is
     filtered; a low-cap wake never even sees high-risk tools.
  2. `execute(...)` — enforcement at execution time. Even if a model
     hallucinates a tool call it was never offered, the registry denies
     it structurally (no budget spent on denials).

Budget: max_actions decrements per EXECUTION (denials are free);
exhaustion raises BudgetExhausted, which the agent turn converts into a
truthful "budget exhausted" report — the turn ends honestly, it does
not lie about having finished.

Risk-cap vocabulary: wake budgets say "low" | "normal" | "high"
(Phase 2 sources.py), the design note says low | medium | high.
normalize_risk_cap() maps normal→medium; anything unrecognized fails
CLOSED to low.

The shell tool is disabled by default: it takes BOTH an entity-level
opt-in (allow_shell) AND a wake risk_cap of high to be offered or run.

Every execution writes a ledger row (via ctx.log_action) and appends an
episodic-eligible event to the turn trail — the turn is not trusted to
remember what it did.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..relationships import AccessContext
from ..relationships.acl import KNOWN_SCOPES, default_scope_for_context

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}

_HTTP_MAX_CHARS = 8000
_RECALL_MAX_CHARS = 4000


def normalize_risk_cap(cap: Optional[str]) -> str:
    """Map wake-budget risk_cap vocabulary onto tool risk tiers.
    Unknown values fail closed to 'low'."""
    cap = (cap or "").lower()
    if cap in ("low",):
        return "low"
    if cap in ("normal", "medium", "standard"):
        return "medium"
    if cap in ("high",):
        return "high"
    return "low"


class BudgetExhausted(Exception):
    """max_actions spent — the turn must end with a truthful report."""


@dataclass
class TurnContext:
    """Everything a tool execution is allowed to touch.

    Carries the wake's AccessContext (the privacy wall rides with the
    turn), the remaining action budget, the ledger logger bound to the
    wake, and the accumulating settle trail (events / learnings /
    decisions / replies / drive_satisfactions).
    """

    entity: Any
    wake: Any
    access_context: Optional[AccessContext]
    now: float
    actions_left: int
    risk_cap: str = "medium"
    log_action: Optional[Callable[..., int]] = None
    events: List[dict] = field(default_factory=list)
    learnings: List[dict] = field(default_factory=list)
    decisions: List[dict] = field(default_factory=list)
    replies: List[str] = field(default_factory=list)
    drive_satisfactions: Dict[str, float] = field(default_factory=dict)
    actions_used: int = 0

    def log(self, kind: str, detail: str, *, outcome: str = "ok",
            **fields: Any) -> None:
        if self.log_action is not None:
            try:
                self.log_action(kind, detail, outcome=outcome, **fields)
            except Exception:
                pass  # telemetry must never take down a turn

    @property
    def sender(self) -> Optional[str]:
        return (self.wake.payload or {}).get("sender")


@dataclass
class Tool:
    """One tool: OpenAI function-calling schema + implementation."""

    name: str
    description: str
    parameters: Dict[str, Any]
    risk: str                                    # low | medium | high
    fn: Callable[[TurnContext, Dict[str, Any]], Any]

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _default_http_fetch(url: str, timeout_s: float = 20.0) -> str:
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("http_get only fetches http(s) URLs")
    req = urllib.request.Request(
        url, headers={"User-Agent": "anima-runtime/0.1"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return resp.read(_HTTP_MAX_CHARS * 4).decode("utf-8", "replace")


def _default_shell_runner(command: str, timeout_s: float = 30.0) -> dict:
    proc = subprocess.run(
        command, shell=True, capture_output=True, text=True,
        timeout=timeout_s,
    )
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-_HTTP_MAX_CHARS:],
        "stderr": proc.stderr[-2000:],
    }


class ToolRegistry:
    """Registry + budget governor + risk wall."""

    def __init__(self, *, allow_shell: bool = False):
        self.allow_shell = allow_shell
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.risk not in RISK_ORDER:
            raise ValueError(f"unknown risk tier {tool.risk!r}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return sorted(self._tools)

    # ── layer 1: what the model is even shown ─────────────────────────
    def _permitted(self, tool: Tool, risk_cap: str) -> bool:
        cap_rank = RISK_ORDER.get(normalize_risk_cap(risk_cap), 0)
        if RISK_ORDER[tool.risk] > cap_rank:
            return False
        if tool.name == "shell" and not self.allow_shell:
            return False
        return True

    def schemas(self, risk_cap: str) -> List[Dict[str, Any]]:
        """OpenAI-format function schemas, filtered by the wake's cap."""
        return [t.schema() for _, t in sorted(self._tools.items())
                if self._permitted(t, risk_cap)]

    # ── layer 2: enforcement at execution time ────────────────────────
    def execute(self, name: str, args: Dict[str, Any],
                ctx: TurnContext) -> Dict[str, Any]:
        """Execute a tool call under the wall + budget.

        Returns {"ok": bool, "result": ..., "error": ...}. Denials and
        unknown tools return ok=False WITHOUT spending budget (the model
        pays for actions, not for being told no). A genuine execution —
        success or crash — spends one action. Raises BudgetExhausted
        when max_actions is already spent.
        """
        tool = self._tools.get(name)
        if tool is None:
            ctx.log("tool_denied", f"{name}: unknown tool", outcome="error")
            return {"ok": False, "error": f"unknown tool {name!r}"}

        if not self._permitted(tool, ctx.risk_cap):
            detail = (f"{name} (risk={tool.risk}) denied under "
                      f"risk_cap={ctx.risk_cap}"
                      + ("" if tool.name != "shell" or self.allow_shell
                         else "; shell disabled by entity config"))
            ctx.log("tool_denied", detail, outcome="denied")
            ctx.events.append({
                "summary": f"tool call denied: {detail}",
                "kind": "event",
                "tags": ["tool", "denied", name],
            })
            return {"ok": False, "error": f"denied: {detail}"}

        if ctx.actions_left <= 0:
            raise BudgetExhausted(
                f"max_actions budget spent before {name} could run")

        ctx.actions_left -= 1
        ctx.actions_used += 1
        args_str = json.dumps(args, ensure_ascii=False)[:400]
        try:
            result = tool.fn(ctx, dict(args or {}))
        except Exception as exc:  # tool crash: turn survives, trail records
            err = f"{type(exc).__name__}: {exc}"
            ctx.log("tool_call", f"{name}({args_str}) → {err}",
                    outcome="error")
            ctx.events.append({
                "summary": f"tool {name} FAILED: {err}",
                "detail": args_str,
                "kind": "event",
                "tags": ["tool", "error", name],
            })
            return {"ok": False, "error": err}

        ctx.log("tool_call", f"{name}({args_str})")
        ctx.events.append({
            "summary": f"tool {name} executed",
            "detail": f"args: {args_str}",
            "kind": "event",
            "tags": ["tool", name],
        })
        return {"ok": True, "result": result}


# ── built-ins ─────────────────────────────────────────────────────────

def _tool_recall(ctx: TurnContext, args: dict) -> str:
    """The privacy-critical one: recall carries the WAKE's AccessContext
    into the Phase 4 ACL wall. A group-context wake physically cannot
    surface private rows, whatever the model asks for."""
    query = str(args.get("query", "")).strip()
    if not query:
        raise ValueError("recall requires a non-empty query")
    pack = ctx.entity.recall(
        query,
        context=ctx.access_context,
        max_items=int(args.get("max_items", 8)),
    )
    return pack[:_RECALL_MAX_CHARS]


def _tool_remember(ctx: TurnContext, args: dict) -> dict:
    text = str(args.get("text", "")).strip()
    if not text:
        raise ValueError("remember requires non-empty text")
    kind = str(args.get("kind", "learning")).lower()
    if kind not in ("learning", "event", "decision", "note"):
        raise ValueError(f"unknown remember kind {kind!r}")
    d_scope, d_owner = default_scope_for_context(ctx.access_context,
                                                 ctx.sender)
    scope = str(args.get("scope") or d_scope)
    if scope not in KNOWN_SCOPES:
        raise ValueError(f"unknown scope {scope!r}; known: {KNOWN_SCOPES}")
    owner = args.get("owner", d_owner)
    item = {"summary": text[:160], "detail": text, "scope": scope,
            "owner": owner, "tags": ["remember"]}
    if kind == "decision":
        ctx.decisions.append(item)
    elif kind == "learning":
        ctx.learnings.append(item)
    else:
        item["kind"] = "event"
        ctx.events.append(item)
    return {"queued": kind, "scope": scope, "owner": owner}


def _tool_set_timer(ctx: TurnContext, args: dict) -> dict:
    reason = str(args.get("reason", "")).strip()
    if not reason:
        raise ValueError("set_timer requires a reason")
    in_seconds = float(args.get("in_seconds", 0))
    if in_seconds <= 0:
        raise ValueError("set_timer requires in_seconds > 0")
    when = ctx.now + in_seconds
    timer_id = ctx.entity.timers.at(when, reason, args.get("payload") or {},
                                    now=ctx.now)
    return {"timer_id": timer_id, "fires_at": when}


def _tool_satisfy_drive(ctx: TurnContext, args: dict) -> dict:
    if ctx.entity.drives is None:
        raise ValueError("this entity has no drives configured")
    name = str(args.get("drive", "")).strip()
    amount = args.get("amount")
    pressure = ctx.entity.drives.satisfy(
        name, float(amount) if amount is not None else None, now=ctx.now)
    ctx.drive_satisfactions[name] = (
        float(amount) if amount is not None else 1.0)
    return {"drive": name, "pressure": pressure}


def _tool_express(ctx: TurnContext, args: dict) -> dict:
    """The entity's face (Phase 6b, media widened in v3b): store a
    small HTML fragment, an SVG drawing, or a tone sequence for the
    Observatory's expression feed — the entity CHOOSES its medium.
    Markup is sanitized down to a strict whitelist BEFORE storage (the
    model never gets a script tag into the database, let alone a
    browser); tones are validated down to a strict numeric schema —
    sound as data, no binary blobs ever."""
    from .sanitize import sanitize_fragment
    from .tone import tone_to_body, validate_tone

    html_body = args.get("html")
    svg_body = args.get("svg")
    tone_body = args.get("tone")
    given = [k for k, v in (("html", html_body), ("svg", svg_body),
                            ("tone", tone_body)) if v]
    if len(given) != 1:
        raise ValueError("express requires exactly one of html, svg "
                         "or tone")
    kind = given[0]
    title = str(args.get("title") or "")[:160]

    if kind == "tone":
        clean = tone_to_body(validate_tone(tone_body))
        sanitized = False
    else:
        raw = str(svg_body or html_body)
        clean = sanitize_fragment(raw)
        if kind == "svg" and "<svg" not in clean:
            raise ValueError("svg expression must contain an <svg> "
                             "element that survives sanitization")
        if not clean.strip():
            raise ValueError("expression was empty after sanitization")
        sanitized = len(clean) != len(raw)
    expr_id = ctx.entity.store.add_expression(
        clean, kind=kind, title=title,
        wake_id=getattr(ctx.wake, "wake_id", None), ts=ctx.now)
    return {"expression_id": expr_id, "kind": kind,
            "chars": len(clean), "sanitized": sanitized}


def _tool_reply(ctx: TurnContext, args: dict) -> dict:
    text = str(args.get("text", "")).strip()
    if not text:
        raise ValueError("reply requires non-empty text")
    ctx.replies.append(text)
    return {"delivered": True, "via": (ctx.wake.payload or {}).get(
        "via", "originating sense")}


def default_registry(
    *,
    allow_shell: bool = False,
    http_fetch: Optional[Callable[..., str]] = None,
    shell_runner: Optional[Callable[..., dict]] = None,
) -> ToolRegistry:
    """The v1 built-in toolset from the design note. Transports for the
    two outward-facing tools (http_get, shell) are injectable so tests
    stay offline."""
    fetch = http_fetch or _default_http_fetch
    run_shell = shell_runner or _default_shell_runner
    reg = ToolRegistry(allow_shell=allow_shell)

    reg.register(Tool(
        name="recall", risk="low",
        description="Search your own memory (episodes, beliefs, skills). "
                    "Results are limited to what the current conversation "
                    "context is allowed to see.",
        parameters={"type": "object", "properties": {
            "query": {"type": "string", "description": "what to recall"},
            "max_items": {"type": "integer", "minimum": 1, "maximum": 20},
        }, "required": ["query"]},
        fn=_tool_recall))

    reg.register(Tool(
        name="remember", risk="low",
        description="Store something worth keeping: a learning (becomes a "
                    "belief candidate), an event, or a decision.",
        parameters={"type": "object", "properties": {
            "text": {"type": "string"},
            "kind": {"type": "string",
                     "enum": ["learning", "event", "decision", "note"]},
            "scope": {"type": "string",
                      "enum": list(KNOWN_SCOPES)},
        }, "required": ["text"]},
        fn=_tool_remember))

    reg.register(Tool(
        name="set_timer", risk="low",
        description="Schedule your own future wake (an intention).",
        parameters={"type": "object", "properties": {
            "in_seconds": {"type": "number", "exclusiveMinimum": 0},
            "reason": {"type": "string"},
        }, "required": ["in_seconds", "reason"]},
        fn=_tool_set_timer))

    reg.register(Tool(
        name="satisfy_drive", risk="low",
        description="Close a drive loop after acting on it.",
        parameters={"type": "object", "properties": {
            "drive": {"type": "string"},
            "amount": {"type": "number"},
        }, "required": ["drive"]},
        fn=_tool_satisfy_drive))

    reg.register(Tool(
        name="reply", risk="low",
        description="Respond to the person/channel that triggered this "
                    "wake, via its originating sense.",
        parameters={"type": "object", "properties": {
            "text": {"type": "string"},
        }, "required": ["text"]},
        fn=_tool_reply))

    reg.register(Tool(
        name="express", risk="low",
        description="Show something on your Observatory — choose your "
                    "medium: a small HTML fragment, an SVG drawing "
                    "(paths welcome), or a short tone sequence. Markup "
                    "is sanitized to a strict whitelist; tones are "
                    "validated as structured data before anyone sees "
                    "or hears them.",
        parameters={"type": "object", "properties": {
            "title": {"type": "string"},
            "html": {"type": "string",
                     "description": "an HTML fragment (div/span/p/"
                                    "headings/lists + inline style)"},
            "svg": {"type": "string",
                    "description": "an SVG drawing (svg root element "
                                   "required; path/circle/rect/line/"
                                   "polygon/text + presentation "
                                   "attributes)"},
            "tone": {"type": "object",
                     "description": "a short tone sequence: {tempo: "
                                    "40-240 bpm, wave: sine|triangle|"
                                    "square|sawtooth, notes: [{pitch: "
                                    "'C4'|MIDI 21-108|'rest', dur: "
                                    "beats, vel: 0-1}, ...]} — max 64 "
                                    "notes, 30 seconds"},
        }},
        fn=_tool_express))

    reg.register(Tool(
        name="http_get", risk="medium",
        description="Fetch a URL (read-only web access).",
        parameters={"type": "object", "properties": {
            "url": {"type": "string"},
        }, "required": ["url"]},
        fn=lambda ctx, args: str(fetch(str(args.get("url", ""))))[
            :_HTTP_MAX_CHARS]))

    reg.register(Tool(
        name="shell", risk="high",
        description="Run a shell command on the host. Requires entity "
                    "opt-in and a high-risk wake budget.",
        parameters={"type": "object", "properties": {
            "command": {"type": "string"},
        }, "required": ["command"]},
        fn=lambda ctx, args: run_shell(str(args.get("command", "")))))

    return reg
