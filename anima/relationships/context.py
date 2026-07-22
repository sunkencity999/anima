"""AccessContext: who is in the room when memory is touched.

Every memory READ requires one (ACL-enforced path); every memory WRITE
may carry scope/owner derived from one. A context is a value object —
immutable, serializable (it rides inside wake payloads), and validated
at construction so an unknown kind can never reach the ACL compiler
(where it would be denied anyway — defense in depth).

Kinds:
    direct — one-on-one with identified person(s). The ONLY kind in
             which private-scoped rows can surface, and only rows owned
             by someone actually present.
    group  — multiple people, or any shared room. Private rows are
             structurally invisible here regardless of who is present.
    public — unauthenticated / broadcast surface. Only public scope.
    system — the entity's own runtime organs (consolidation daemon,
             settle-phase, self-audit). Sees everything: the organism
             is allowed to know its own mind.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Tuple

KINDS = ("direct", "group", "public", "system")


def _ctx_id(prefix: str) -> str:
    return f"ctx-{prefix}-{uuid.uuid4().hex[:10]}"


@dataclass(frozen=True)
class AccessContext:
    """The access context a memory operation happens within."""

    context_id: str
    kind: str                                # direct | group | public | system
    participants: Tuple[str, ...] = ()       # person_ids present
    channel: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(
                f"unknown access-context kind {self.kind!r}; known: {KINDS}")
        # Normalize participants to a deduped, ordered tuple of str.
        seen: list[str] = []
        for p in self.participants:
            p = str(p)
            if p not in seen:
                seen.append(p)
        object.__setattr__(self, "participants", tuple(seen))

    # ── constructors for the common cases ─────────────────────────────
    @classmethod
    def direct(cls, person_id: str, *, channel: str = "chat",
               context_id: Optional[str] = None,
               extra_participants: Iterable[str] = ()) -> "AccessContext":
        """One-on-one context with person_id (plus optional co-present
        identified people — still kind=direct only if you mean it)."""
        return cls(
            context_id=context_id or _ctx_id("direct"),
            kind="direct",
            participants=(person_id, *extra_participants),
            channel=channel,
        )

    @classmethod
    def group(cls, participants: Iterable[str], *, channel: str = "group",
              context_id: Optional[str] = None) -> "AccessContext":
        return cls(
            context_id=context_id or _ctx_id("group"),
            kind="group",
            participants=tuple(participants),
            channel=channel,
        )

    @classmethod
    def public(cls, *, channel: str = "public",
               context_id: Optional[str] = None) -> "AccessContext":
        return cls(
            context_id=context_id or _ctx_id("public"),
            kind="public",
            participants=(),
            channel=channel,
        )

    @classmethod
    def system(cls, *, channel: str = "runtime",
               context_id: str = "ctx-system") -> "AccessContext":
        return cls(context_id=context_id, kind="system",
                   participants=(), channel=channel)

    # ── (de)serialization — rides inside wake payloads ────────────────
    def to_dict(self) -> Dict[str, Any]:
        return {
            "context_id": self.context_id,
            "kind": self.kind,
            "participants": list(self.participants),
            "channel": self.channel,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AccessContext":
        return cls(
            context_id=d.get("context_id") or _ctx_id(d.get("kind", "unk")),
            kind=d["kind"],
            participants=tuple(d.get("participants") or ()),
            channel=d.get("channel", ""),
        )
